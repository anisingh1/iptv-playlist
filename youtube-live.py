import subprocess
import json
import logging
import socket
import threading
import time
import hashlib
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from flask import Flask, request, Response, jsonify
from urllib.parse import unquote, quote
import os

# Ensure UTF-8 encoding for subprocesses
os.environ['PYTHONIOENCODING'] = 'utf-8'

app = Flask(__name__)

# Set up logging with UTF-8 encoding
logging.basicConfig(level=logging.INFO, encoding='utf-8')

DEFAULT_INPUT_XML = 'youtubelinks.xml'
DEFAULT_OUTPUT_M3U = 'youtubelive.m3u'
DEFAULT_PORT = 6095
DEFAULT_SYNC_INTERVAL_SECONDS = 86400
SOURCE_XML_URL = 'https://github.com/anisingh1/iptv-playlist/blob/main/youtubelinks.xml'
CACHE_REFRESH_INTERVAL_SECONDS = 3600
CACHE_TTL_SECONDS = 3600

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
STREAM_CACHE = {}
STREAM_CACHE_LOCK = threading.Lock()

def file_hash(file_path):
    """Return SHA-256 hash of a file, or None if missing."""
    try:
        with open(file_path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.error('Failed to hash %s: %s', file_path, exc)
        return None

def to_raw_github_url(url):
    """Convert a GitHub blob URL to its raw content URL."""
    marker = 'https://github.com/'
    if not url.startswith(marker):
        return url
    parts = url[len(marker):].split('/')
    if len(parts) >= 5 and parts[2] == 'blob':
        owner = parts[0]
        repo = parts[1]
        branch = parts[3]
        path = '/'.join(parts[4:])
        return f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}'
    return url

def fetch_remote_xml(url):
    """Fetch XML content from the remote URL."""
    raw_url = to_raw_github_url(url)
    try:
        with urllib.request.urlopen(raw_url, timeout=20) as response:
            return response.read()
    except urllib.error.URLError as exc:
        logging.warning('Failed to fetch %s: %s', raw_url, exc)
        return None
    except Exception as exc:
        logging.warning('Unexpected fetch error: %s', exc)
        return None

def get_cached_stream(url):
    with STREAM_CACHE_LOCK:
        entry = STREAM_CACHE.get(url)
        if not entry:
            return None
        if time.time() - entry['timestamp'] > CACHE_TTL_SECONDS:
            return None
        return entry['stream_url']

def set_cached_stream(url, stream_url):
    with STREAM_CACHE_LOCK:
        STREAM_CACHE[url] = {
            'stream_url': stream_url,
            'timestamp': time.time()
        }

def resolve_stream_url(youtube_url):
    """Resolve a stream URL using Streamlink, with youtube-dl fallback."""
    try:
        info_command = ['streamlink', '--json', '--loglevel', 'debug', youtube_url]
        info_process = subprocess.Popen(info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info_output, info_error = info_process.communicate()

        if info_process.returncode != 0:
            error_msg = info_error.decode('utf-8', errors='replace')
            logging.error(f'Streamlink error: {error_msg}')
            return None

        stream_info = json.loads(info_output.decode('utf-8', errors='replace'))
        if 'streams' not in stream_info or not stream_info['streams']:
            if 'youtube.com' in youtube_url.lower() or 'youtu.be' in youtube_url.lower():
                yt_command = ['youtube-dl', '--get-url', '--youtube-skip-dash-manifest', youtube_url]
                yt_process = subprocess.Popen(yt_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                yt_url, yt_error = yt_process.communicate()

                if yt_process.returncode != 0:
                    logging.error(
                        f"youtube-dl error: {yt_error.decode('utf-8', errors='replace')}"
                    )
                    return None

                return yt_url.decode('utf-8', errors='replace').strip()
            return None

        best_quality = stream_info['streams'].get('best')
        if not best_quality:
            return None

        return best_quality.get('url')
    except Exception as exc:
        logging.error(f'Error resolving stream URL: {str(exc)}')
        return None

def refresh_stream_cache_loop(interval_seconds):
    """Refresh cached stream URLs on a schedule."""
    xml_path = os.path.join(REPO_ROOT, DEFAULT_INPUT_XML)
    while True:
        try:
            if os.path.exists(xml_path):
                channels = parse_channel_xml(xml_path) or []
                for channel in channels:
                    youtube_url = channel.get('youtube-url')
                    if not youtube_url:
                        continue
                    stream_url = resolve_stream_url(youtube_url)
                    if stream_url:
                        set_cached_stream(youtube_url, stream_url)
                        logging.info('Cached stream URL for %s', channel.get('name', youtube_url))
            else:
                logging.warning('Cache refresh: %s not found.', xml_path)
        except Exception as exc:
            logging.error('Cache refresh error: %s', exc)

        time.sleep(interval_seconds)

def sync_and_regenerate_loop(interval_seconds):
    """Periodically fetch remote XML and regenerate M3U if it changes."""
    xml_path = os.path.join(REPO_ROOT, DEFAULT_INPUT_XML)
    last_hash = file_hash(xml_path)
    if last_hash is None:
        logging.warning('Sync loop: %s not found. Waiting for remote file.', xml_path)

    while True:
        try:
            remote_content = fetch_remote_xml(SOURCE_XML_URL)
            if remote_content:
                remote_hash = hashlib.sha256(remote_content).hexdigest()
                if remote_hash != last_hash:
                    with open(xml_path, 'wb') as handle:
                        handle.write(remote_content)
                    logging.info('Downloaded updated %s from remote source.', DEFAULT_INPUT_XML)
                    host_ip = get_host_ip()
                    generate_m3u_from_xml(xml_path, DEFAULT_OUTPUT_M3U, host_ip, DEFAULT_PORT)
                    last_hash = remote_hash
        except Exception as exc:
            logging.error('Sync loop error: %s', exc)

        time.sleep(interval_seconds)

def get_host_ip():
    """Return the local LAN IP address used for outbound traffic."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except Exception as exc:
        logging.warning(f'Failed to resolve local IP, falling back to localhost: {exc}')
        return '127.0.0.1'
    finally:
        try:
            sock.close()
        except Exception:
            pass

def parse_channel_xml(file_path):
    """Parse channel info from an XML file."""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        channels = []
        for channel in root.findall('channel'):
            channel_info = {
                'name': (channel.find('channel-name').text or '').strip()
                if channel.find('channel-name') is not None else 'Unknown',
                'tvg-id': (channel.find('tvg-id').text or '').strip()
                if channel.find('tvg-id') is not None else '',
                'tvg-name': (channel.find('tvg-name').text or '').strip()
                if channel.find('tvg-name') is not None else '',
                'tvg-logo': (channel.find('tvg-logo').text or '').strip()
                if channel.find('tvg-logo') is not None else '',
                'group-title': (channel.find('group-title').text or '').strip()
                if channel.find('group-title') is not None else 'General',
                'youtube-url': (channel.find('youtube-url').text or '').strip()
                if channel.find('youtube-url') is not None else ''
            }
            if channel_info['youtube-url']:
                channels.append(channel_info)
            else:
                logging.warning(
                    "Skipping channel '%s' due to missing YouTube URL.",
                    channel_info['name']
                )
        return channels
    except Exception as exc:
        logging.error(f'Failed to parse XML file {file_path}: {exc}')
        return None

def generate_m3u_from_xml(input_xml, output_m3u, host_ip, port):
    """Generate youtubelive.m3u from the input XML file."""
    channels = parse_channel_xml(input_xml)
    if not channels:
        logging.error('No channels found. Skipping M3U generation.')
        return False

    try:
        with open(output_m3u, 'w', encoding='utf-8') as m3u:
            m3u.write('#EXTM3U\n')
            for channel in channels:
                m3u.write(
                    f'#EXTINF:-1 tvg-id="{channel["tvg-id"]}" '
                    f'tvg-name="{channel["tvg-name"]}" '
                    f'tvg-logo="{channel["tvg-logo"]}" '
                    f'group-title="{channel["group-title"]}",'
                    f'{channel["name"]}\n'
                )
                stream_url = quote(channel['youtube-url'], safe=':/?=&')
                m3u.write(
                    f'http://{host_ip}:{port}/stream?url={stream_url}\n'
                )
        logging.info('Generated %s from %s', output_m3u, input_xml)
        return True
    except Exception as exc:
        logging.error(f'Failed to generate M3U file {output_m3u}: {exc}')
        return False

@app.route('/stream', methods=['GET'])
def stream():
    url = unquote(request.args.get('url'))  # Decode URL-encoded characters
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400

    try:
        cached_stream = get_cached_stream(url)
        if cached_stream:
            stream_target = cached_stream
            logging.info('Using cached stream URL for %s', url)
        else:
            stream_target = resolve_stream_url(url)
            if not stream_target:
                return jsonify({'error': 'No valid streams found'}), 404
            set_cached_stream(url, stream_target)

        # Command to run Streamlink
        command = [
            'streamlink',
            stream_target,
            'best',
            '--hls-live-restart',
            '--stdout'
        ]

        # Start the subprocess
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        client_ip = request.remote_addr

        def generate():
            try:
                logging.info(f"Starting stream for client {client_ip} from {url}")
                while True:
                    data = process.stdout.read(4096)
                    if not data:
                        break
                    yield data
            except GeneratorExit:
                logging.info(f"Client {client_ip} disconnected from stream {url}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                finally:
                    process.stdout.close()
                    process.stderr.close()
            except Exception as e:
                logging.error(f'Error in generator for {client_ip}: {str(e)}')
                process.terminate()
                process.stdout.close()
                process.stderr.close()

        response = Response(generate(), content_type='video/mp2t')
        
        @response.call_on_close
        def cleanup():
            if process.poll() is None:
                logging.info(f"Cleaning up stream process for client {client_ip} from {url}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                finally:
                    process.stdout.close()
                    process.stderr.close()

        return response

    except Exception as e:
        logging.error(f'Error occurred: {str(e)}')
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    input_xml = DEFAULT_INPUT_XML
    if os.path.exists(input_xml):
        host_ip = get_host_ip()
        generate_m3u_from_xml(input_xml, DEFAULT_OUTPUT_M3U, host_ip, DEFAULT_PORT)
    else:
        logging.error(
            'Input file not found: %s',
            DEFAULT_INPUT_XML
        )

    sync_thread = threading.Thread(
        target=sync_and_regenerate_loop,
        args=(DEFAULT_SYNC_INTERVAL_SECONDS,),
        daemon=True
    )
    sync_thread.start()

    cache_thread = threading.Thread(
        target=refresh_stream_cache_loop,
        args=(CACHE_REFRESH_INTERVAL_SECONDS,),
        daemon=True
    )
    cache_thread.start()

    app.run(host='0.0.0.0', port=DEFAULT_PORT)
