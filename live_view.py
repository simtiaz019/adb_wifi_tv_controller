"""Optional scrcpy-server H.264 -> FFmpeg stream-copy fMP4 bridge.

No decoding/transcoding on the Pi. One capture per device, bounded shared fragments,
short-lived viewer leases. The owning worker always removes its ADB forward.
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time

from app_activity import boolean, integer

SCRCPY_VERSION = '5.0.1'
SCRCPY_SHA256 = '764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536'
MAX_BOX = 4 * 1024 * 1024


@dataclass
class LiveSettings:
    enabled: bool = False
    fps: int = 15
    width: int = 1280
    bitrate: int = 2000000
    idle: int = 60
    max_devices: int = 1
    max_viewers: int = 4
    startup: int = 30
    server_path: str = '/opt/tv-hub/vendor/scrcpy-server-v5.0.1'
    ffmpeg: str = 'ffmpeg'

    @classmethod
    def load(cls, env):
        return cls(boolean(env, 'LIVE_VIEW_ENABLED', False),
                   integer(env, 'LIVE_VIEW_MAX_FPS', 15, 5, 30),
                   integer(env, 'LIVE_VIEW_MAX_WIDTH', 1280, 320, 1920),
                   integer(env, 'LIVE_VIEW_BITRATE', 2000000, 100000, 8000000),
                   integer(env, 'LIVE_VIEW_IDLE_TIMEOUT', 60, 10, 300),
                   integer(env, 'LIVE_VIEW_MAX_CONCURRENT_DEVICES', 1, 1, 2),
                   integer(env, 'LIVE_VIEW_MAX_VIEWERS', 4, 1, 8),
                   integer(env, 'LIVE_VIEW_STARTUP_TIMEOUT', 30, 10, 120),
                   env.get('LIVE_VIEW_SERVER_PATH', cls.server_path), env.get('LIVE_VIEW_FFMPEG', 'ffmpeg'))

    def availability(self):
        if not self.enabled:
            return 'Live View is disabled. Enable LIVE_VIEW_ENABLED in the Pi configuration.'
        if not shutil.which(self.ffmpeg):
            return 'FFmpeg is missing. Install the optional Live View dependencies.'
        try:
            if hashlib.sha256(Path(self.server_path).read_bytes()).hexdigest() != SCRCPY_SHA256:
                return 'scrcpy-server checksum does not match the pinned version. Reinstall Live View dependencies.'
        except OSError:
            return 'scrcpy-server is missing. Run install-live.sh on the Pi.'
        return None


def ffmpeg_command(settings):
    return [settings.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-fflags', '+genpts', '-probesize', '65536', '-analyzeduration', '1000000',
            '-f', 'h264', '-r', str(settings.fps), '-i', 'pipe:0', '-map', '0:v:0',
            '-an', '-c:v', 'copy', '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-flush_packets', '1', '-f', 'mp4', 'pipe:1']


def boxes(stream, stopped, timeout=20):
    """Read bounded ISO BMFF boxes without unbounded blocking on a dead encoder."""
    def read(size):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while len(data) < size:
            if stopped.is_set():
                raise InterruptedError
            if time.monotonic() > deadline:
                raise TimeoutError('No video packets received')
            if not select.select([stream], [], [], .25)[0]:
                continue
            part = os.read(stream.fileno(), size - len(data))
            if not part:
                raise EOFError('Video stream ended')
            data.extend(part)
        return bytes(data)
    while not stopped.is_set():
        header = read(8)
        size, kind = struct.unpack('>I4s', header)
        if not 8 <= size <= MAX_BOX:
            raise ValueError('Invalid or oversized MP4 fragment')
        yield kind, header + read(size - 8)


class Capture:
    def __init__(self, tv, settings):
        self.tv, self.settings = tv, settings
        self.stopped = threading.Event()
        self.done = threading.Event()
        self.condition = threading.Condition()
        self.phase, self.error, self.codec = 'starting', None, None
        self.init = b''
        self.fragments = deque(maxlen=4)
        self.bootstrap = None
        self.sequence = 0
        self.thread = None
        self.processes = []
        self.address = None
        self.port = None
        self.sock = None
        self.device_path = None
        self.started = time.monotonic()

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='live-capture')
        self.thread.start()

    def stop(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()

    def publish(self, kind, data):
        with self.condition:
            if kind == b'ftyp':
                self.init = data
            elif kind == b'moov':
                self.init += data
                index = data.find(b'avcC')
                if index < 0 or index + 8 > len(data):
                    raise ValueError('H.264 configuration missing')
                self.codec = 'avc1.' + data[index + 5:index + 8].hex()
            elif kind == b'mdat':
                self.sequence += 1
                self.fragments.append((self.sequence, data))
                if self.bootstrap is None and self.init:
                    self.bootstrap = (self.sequence, data)
                self.phase = 'receiving'  # Transport only; browser verifies decoded frames.
            self.condition.notify_all()

    def segment(self, after):
        with self.condition:
            self.condition.wait_for(lambda: (self.sequence > after and self.bootstrap is not None and self.codec) or
                                    self.error or self.done.is_set(), timeout=3)
            if self.error:
                return 503, {'error': self.error}
            if self.sequence <= after:
                return (410, {'error': 'Stream stopped.'}) if self.done.is_set() else (204, None)
            if after == 0:
                if self.bootstrap is None:
                    return 204, None
                sequence, data = self.bootstrap
            else:
                sequence, data = next(((n, b) for n, b in self.fragments if n > after), self.fragments[-1])
            return 200, (sequence, self.codec, (self.init if after == 0 else b'') + data)

    def adb(self, *args, timeout=8):
        if self.stopped.is_set():
            raise InterruptedError
        result = subprocess.run(['adb', '-s', self.address, *args], capture_output=True, timeout=timeout)
        if result.returncode:
            raise OSError('ADB command failed')
        return result.stdout.decode(errors='replace').strip()

    def spawn(self, args, **kwargs):
        # Use nice without preexec_fn (unsafe in this threaded web process).
        command = ['nice', '-n', '10', *args] if shutil.which('nice') else args
        process = subprocess.Popen(command, stderr=subprocess.DEVNULL, start_new_session=True, **kwargs)
        self.processes.append(process)
        return process

    def run(self):
        pump = None
        try:
            # Imported lazily to avoid an import cycle; separate ADB instance from remote.
            from tv_controller import ADB
            self.address = ADB().connect(self.tv, cancelled=lambda: self.stopped.is_set() or time.monotonic() - self.started > self.settings.startup)
            if not self.address or self.stopped.is_set():
                raise OSError('Device is unreachable')
            suffix = secrets.token_hex(4)
            scid = int(suffix, 16) & 0x7fffffff
            self.device_path = f'/data/local/tmp/tvhub-{suffix}.jar'
            self.adb('push', self.settings.server_path, self.device_path)
            self.port = int(self.adb('forward', 'tcp:0', f'localabstract:scrcpy_{scid:08x}'))
            s = self.settings
            command = ['adb', '-s', self.address, 'shell', f'CLASSPATH={self.device_path}',
                       'app_process', '/', 'com.genymobile.scrcpy.Server', SCRCPY_VERSION,
                       f'scid={scid:08x}', 'log_level=error', 'audio=false', 'control=false',
                       'tunnel_forward=true', 'raw_stream=true', 'video_codec=h264', 'power_on=false',
                       f'max_size={s.width}', f'max_fps={s.fps}', f'video_bit_rate={s.bitrate}',
                       f'video_codec_options=i-frame-interval:int=1,repeat-previous-frame-after:long={1000000 // s.fps}']
            server = self.spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            deadline = self.started + s.startup
            # raw_stream disables the dummy byte. Peek actual H.264 bytes before
            # accepting a forward connection (ADB may close it while server boots).
            while time.monotonic() < deadline and not self.stopped.is_set():
                if server.poll() is not None:
                    raise OSError('Capture server exited')
                try:
                    candidate = socket.create_connection(('127.0.0.1', self.port), timeout=1)
                except OSError:
                    self.stopped.wait(.2)
                    continue
                candidate.settimeout(1)
                try:
                    if candidate.recv(1, socket.MSG_PEEK):
                        self.sock = candidate
                        break
                except socket.timeout:
                    # Socket can be accepted before the encoder produces its first frame.
                    self.sock = candidate
                    break
                candidate.close()
                self.stopped.wait(.2)
            if self.sock is None:
                raise TimeoutError('Capture did not start')
            self.sock.settimeout(2)
            mux = self.spawn(ffmpeg_command(s), stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            def feed():
                try:
                    while not self.stopped.is_set():
                        try:
                            chunk = self.sock.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view and not self.stopped.is_set():
                            count = mux.stdin.write(view)
                            if not count:
                                raise OSError('Muxer input closed')
                            view = view[count:]
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        mux.stdin.close()
                    except (OSError, ValueError):
                        pass
            pump = threading.Thread(target=feed, daemon=True, name='live-video-input')
            pump.start()
            fragment = b''
            for kind, data in boxes(mux.stdout, self.stopped, s.startup):
                if kind == b'moof':
                    fragment = data
                elif kind == b'mdat':
                    if not fragment:
                        raise ValueError('Missing fragment header')
                    self.publish(kind, fragment + data)
                    fragment = b''
                elif kind in (b'ftyp', b'moov'):
                    self.publish(kind, data)
        except Exception:
            if not self.stopped.is_set():
                self.error = 'Capture unavailable or interrupted. Check ADB, encoder support and protected-content restrictions.'
                self.phase = 'error'
        finally:
            self.stopped.set()
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
            for process in reversed(self.processes):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=2)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=2)
                for pipe in (process.stdin, process.stdout):
                    if pipe:
                        pipe.close()
            if pump:
                pump.join(timeout=2)
            if self.address:
                # Close only this capture's socket/tunnel and temporary server copy.
                for args in ([['forward', '--remove', f'tcp:{self.port}']] if self.port else []) + (
                        [['shell', 'rm', '-f', self.device_path]] if self.device_path else []):
                    try:
                        subprocess.run(['adb', '-s', self.address, *args], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if not self.error:
                self.phase = 'stopped'
            self.done.set()
            with self.condition:
                self.condition.notify_all()


class LiveManager:
    def __init__(self, tvs, settings, factory=Capture):
        self.tvs = {tv.id: tv for tv in tvs}
        self.settings, self.factory = settings, factory
        self.sessions, self.viewers = {}, {}
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.janitor = threading.Thread(target=self.watch, daemon=True, name='live-cleanup')
        self.janitor.start()

    def reap(self):
        now = time.monotonic()
        for key, (_, touched) in list(self.viewers.items()):
            if now - touched > self.settings.idle:
                del self.viewers[key]
        active = {id_ for id_, _ in self.viewers.values()}
        for id_, session in list(self.sessions.items()):
            if id_ not in active:
                session.stop()
            if session.phase == 'starting' and now - session.started > self.settings.startup + 10:
                session.error = 'Timed out starting capture. Check device connectivity and video support.'
                session.stop()
            if session.done.is_set() and id_ not in active:
                del self.sessions[id_]

    def watch(self):
        while not self.closed.wait(1):
            with self.lock:
                self.reap()

    def start(self, tv_id):
        tv = self.tvs.get(tv_id)
        if not tv:
            return 404, {'error': 'Unknown device.'}
        if not tv.live_view:
            return 409, {'error': 'Live View is disabled or unsupported for this device. Remote control and monitoring remain available.'}
        error = self.settings.availability()
        if error:
            return 503, {'error': error}
        with self.lock:
            self.reap()
            session = self.sessions.get(tv_id)
            if session and (session.stopped.is_set() or session.done.is_set()):
                return 409, {'error': 'Previous capture is stopping or failed. Stop its viewers, then reconnect.'}
            if len(self.viewers) >= self.settings.max_viewers:
                return 429, {'error': 'Viewer limit reached. Close another Live View first.'}
            if session is None:
                if len(self.sessions) >= self.settings.max_devices:
                    return 409, {'error': 'Another device is streaming. Stop it before starting this device.'}
                session = self.factory(tv, self.settings)
                self.sessions[tv_id] = session
                session.start()
            viewer = secrets.token_urlsafe(24)
            self.viewers[viewer] = (tv_id, time.monotonic())
            return 200, {'viewer': viewer, 'phase': session.phase}

    def get(self, tv_id, viewer):
        with self.lock:
            item = self.viewers.get(viewer)
            if not item or item[0] != tv_id or time.monotonic() - item[1] > self.settings.idle:
                return None
            self.viewers[viewer] = (tv_id, time.monotonic())
            return self.sessions.get(tv_id)

    def stop(self, tv_id, viewer):
        with self.lock:
            if viewer in self.viewers and self.viewers[viewer][0] == tv_id:
                del self.viewers[viewer]
            self.reap()
        return 200, {'message': 'Viewer stopped.'}

    def close(self):
        self.closed.set()
        with self.lock:
            sessions = list(self.sessions.values())
            for session in sessions:
                session.stop()
        self.janitor.join(timeout=2)
        for session in sessions:
            if session.thread:
                session.thread.join(timeout=10)
