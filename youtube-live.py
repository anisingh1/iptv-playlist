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
import shutil
from flask import Flask, request, Response, jsonify
from urllib.parse import unquote, quote
import os

# Ensure UTF-8 encoding for subprocesses
os.environ['PYTHONIOENCODING'] = 'utf-8'

app = Flask(__name__)

# Set up logging with UTF-8 encoding
logging.basicConfig(level=logging.INFO, encoding='utf-8')

# Find streamlink, ffmpeg, and yt-dlp in PATH or venv
STREAMLINK_PATH = shutil.which('streamlink') or 'streamlink'
YT_DLP_PATH = shutil.which('yt-dlp') or 'yt-dlp'
FFMPEG_PATH = shutil.which('ffmpeg') or 'ffmpeg'

logging.info(f'Using streamlink: {STREAMLINK_PATH}')
logging.info(f'Using yt-dlp: {YT_DLP_PATH}')
logging.info(f'Using ffmpeg: {FFMPEG_PATH}')

DEFAULT_INPUT_XML = 'youtubelinks.xml'
DEFAULT_OUTPUT_M3U = 'youtubelive.m3u'
DEFAULT_PORT = 6095
DEFAULT_SYNC_INTERVAL_SECONDS = 86400
SOURCE_XML_URL = 'https://github.com/anisingh1/iptv-playlist/blob/main/youtubelinks.xml'
LOGO_BASE_URL = 'https://raw.githubusercontent.com/anisingh1/iptv-playlist/main/'  # Base URL for logo files
CACHE_REFRESH_INTERVAL_SECONDS = 3600  # 1 hour (cache is only for monitoring/stats now)
CACHE_TTL_SECONDS = 7200  # 2 hours (not critical since we don't use it for streaming)
STARTUP_CACHE_ENABLED = True  # Pre-warm cache on startup
STREAM_QUALITY_PRIORITY = ['1080p', '720p', '480p']  # Try in order: 1080p first, fallback to 720p, then 480p

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
    """Resolve a stream URL using Streamlink with quality fallback (1080p → 720p → 480p)."""
    # Try each quality in priority order
    for quality in STREAM_QUALITY_PRIORITY:
        try:
            logging.info(f'Trying {quality} for {youtube_url}')
            info_command = [STREAMLINK_PATH, '--stream-url', youtube_url, quality]
            info_process = subprocess.Popen(
                info_command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            )
            info_output, info_error = info_process.communicate(timeout=15)

            if info_process.returncode == 0:
                stream_url = info_output.decode('utf-8', errors='replace').strip()
                if stream_url:
                    logging.info(f'✓ Successfully resolved {quality} for {youtube_url}')
                    return stream_url
                    
            # If this quality failed, log it and try next
            error_msg = info_error.decode('utf-8', errors='replace')
            logging.warning(f'{quality} not available for {youtube_url}: {error_msg[:100]}')
            
        except subprocess.TimeoutExpired:
            logging.warning(f'Timeout resolving {quality} for {youtube_url}, trying next quality')
            continue
        except Exception as exc:
            logging.warning(f'Error resolving {quality} for {youtube_url}: {str(exc)}')
            continue
    
    # If all qualities failed, try yt-dlp as last resort
    logging.warning(f'All qualities failed for {youtube_url}, trying yt-dlp fallback')
    if 'youtube.com' in youtube_url.lower() or 'youtu.be' in youtube_url.lower():
        try:
            yt_command = [YT_DLP_PATH, '--get-url', '--no-warnings', youtube_url]
            yt_process = subprocess.Popen(
                yt_command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            )
            yt_url, yt_error = yt_process.communicate(timeout=15)

            if yt_process.returncode == 0:
                urls = yt_url.decode('utf-8', errors='replace').strip().split('\n')
                if urls:
                    logging.info(f'✓ yt-dlp fallback succeeded for {youtube_url}')
                    return urls[0]
            
            logging.error(f"yt-dlp error: {yt_error.decode('utf-8', errors='replace')}")
        except (subprocess.TimeoutExpired, Exception) as e:
            logging.error(f'yt-dlp fallback failed: {e}')
    
    logging.error(f'Failed to resolve any quality for {youtube_url}')
    return None

def refresh_stream_cache_loop(interval_seconds, run_immediately=True):
    """Refresh cached stream URLs on a schedule."""
    xml_path = os.path.join(REPO_ROOT, DEFAULT_INPUT_XML)
    
    # Run immediately on first call if requested (for pre-warming)
    if run_immediately:
        logging.info('Pre-warming stream cache on startup...')
        try:
            if os.path.exists(xml_path):
                channels = parse_channel_xml(xml_path) or []
                success_count = 0
                for channel in channels:
                    youtube_url = channel.get('youtube-url')
                    if not youtube_url:
                        continue
                    stream_url = resolve_stream_url(youtube_url)
                    if stream_url:
                        set_cached_stream(youtube_url, stream_url)
                        success_count += 1
                        logging.info('Pre-cached: %s', channel.get('name', youtube_url))
                    else:
                        logging.warning('Failed to pre-cache: %s', channel.get('name', youtube_url))
                logging.info('Pre-warming complete: %d/%d channels cached', success_count, len(channels))
            else:
                logging.warning('Cannot pre-warm cache: %s not found', xml_path)
        except Exception as exc:
            logging.error('Pre-warming cache error: %s', exc)
    
    # Now continue with periodic refresh
    while True:
        time.sleep(interval_seconds)
        
        try:
            if os.path.exists(xml_path):
                channels = parse_channel_xml(xml_path) or []
                logging.info('Starting scheduled cache refresh for %d channels...', len(channels))
                success_count = 0
                for channel in channels:
                    youtube_url = channel.get('youtube-url')
                    if not youtube_url:
                        continue
                    stream_url = resolve_stream_url(youtube_url)
                    if stream_url:
                        set_cached_stream(youtube_url, stream_url)
                        success_count += 1
                        logging.info('Refreshed cache for %s', channel.get('name', youtube_url))
                    else:
                        logging.warning('Failed to refresh cache for %s', channel.get('name', youtube_url))
                logging.info('Cache refresh complete: %d/%d channels refreshed successfully', success_count, len(channels))
            else:
                logging.warning('Cache refresh: %s not found.', xml_path)
        except Exception as exc:
            logging.error('Cache refresh error: %s', exc)

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

def commit_m3u_to_git(m3u_file):
    """Commit the M3U file to git."""
    try:
        # Check if file has changes
        status_result = subprocess.run(
            ['git', 'status', '--porcelain', m3u_file],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if not status_result.stdout.strip():
            logging.info('No changes to %s, skipping commit.', m3u_file)
            return True
        
        # Add the file
        add_result = subprocess.run(
            ['git', 'add', m3u_file],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if add_result.returncode != 0:
            logging.error('git add failed: %s', add_result.stderr)
            return False
        
        # Commit with timestamp
        commit_msg = f'Auto-update {m3u_file} at {time.strftime("%Y-%m-%d %H:%M:%S")}'
        commit_result = subprocess.run(
            ['git', 'commit', '-m', commit_msg],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if commit_result.returncode != 0:
            logging.error('git commit failed: %s', commit_result.stderr)
            return False
        
        logging.info('Committed %s to git: %s', m3u_file, commit_msg)
        
        # Push to remote
        push_result = subprocess.run(
            ['git', 'push'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if push_result.returncode != 0:
            logging.error('git push failed: %s', push_result.stderr)
            return False
        
        logging.info('Pushed %s to remote repository.', m3u_file)
        return True
        
    except subprocess.TimeoutExpired:
        logging.error('Git operation timed out')
        return False
    except Exception as exc:
        logging.error('Failed to commit to git: %s', exc)
        return False

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
                # Prepend GitHub raw URL to logo if it's a relative path
                logo = channel['tvg-logo']
                if logo and not logo.startswith('http'):
                    logo = LOGO_BASE_URL + logo
                
                m3u.write(
                    f'#EXTINF:-1 tvg-id="{channel["tvg-id"]}" '
                    f'tvg-name="{channel["tvg-name"]}" '
                    f'tvg-logo="{logo}" '
                    f'group-title="{channel["group-title"]}",'
                    f'{channel["name"]}\n'
                )
                stream_url = quote(channel['youtube-url'], safe=':/?=&')
                m3u.write(
                    f'http://{host_ip}:{port}/stream?url={stream_url}\n'
                )
        logging.info('Generated %s from %s', output_m3u, input_xml)
        
        # Commit the M3U file to git
        commit_m3u_to_git(output_m3u)
        
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
        # Check cache for HLS URL
        cached_stream = get_cached_stream(url)
        
        if cached_stream:
            # Fast path: Use cached HLS URL with ffmpeg
            # ffmpeg will handle manifest updates automatically, so no expiry issues!
            logging.info('Using cached HLS URL for %s (fast path)', url)
            command = [
                FFMPEG_PATH,
                '-loglevel', 'error',  # Only show errors
                '-i', cached_stream,    # Input: cached HLS URL
                '-c', 'copy',           # Copy streams without re-encoding
                '-f', 'mpegts',         # Output format
                '-'                     # Output to stdout
            ]
            logging.info(f"Fast path: ffmpeg proxying cached HLS URL")
        else:
            # Slow path: Use streamlink to resolve URL
            # This happens on cache miss or first request
            logging.info('Cache miss for %s, using streamlink (slow path)', url)
            # Use comma-separated quality list for fallback (1080p,720p,480p)
            quality_fallback = ','.join(STREAM_QUALITY_PRIORITY)
            command = [
                STREAMLINK_PATH,
                url,  # Use original YouTube URL
                quality_fallback,  # Try 1080p, fallback to 720p, then 480p
                '--hls-live-restart',
                '--stdout'
            ]
            logging.info(f"Slow path: streamlink with quality fallback {quality_fallback}")
            
            # After first chunk, update cache in background
            # (This will make subsequent requests use fast path)

        # Start the subprocess
        process = subprocess.Popen(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            bufsize=8192
        )
        client_ip = request.remote_addr

        def generate():
            try:
                logging.info(f"Starting stream for client {client_ip} from {url}")
                chunk_count = 0
                first_chunk_time = time.time()
                
                while True:
                    data = process.stdout.read(8192)
                    if not data:
                        # Check if process ended with error
                        return_code = process.poll()
                        if return_code is not None and return_code != 0:
                            stderr_output = process.stderr.read().decode('utf-8', errors='replace')
                            logging.error(f"Stream process failed with code {return_code}: {stderr_output}")
                        else:
                            logging.info(f"Stream ended normally for {client_ip}, sent {chunk_count} chunks")
                        break
                    
                    chunk_count += 1
                    if chunk_count == 1:
                        elapsed = time.time() - first_chunk_time
                        logging.info(f"First chunk sent to {client_ip} in {elapsed:.2f}s, stream is flowing")
                        
                        # If we used streamlink (slow path), cache the result for next time
                        if not cached_stream:
                            # Resolve and cache in background thread so we don't block streaming
                            def cache_in_background():
                                try:
                                    resolved_url = resolve_stream_url(url)
                                    if resolved_url:
                                        set_cached_stream(url, resolved_url)
                                        logging.info(f"Background cached HLS URL for {url}")
                                except Exception as e:
                                    logging.error(f"Background caching failed: {e}")
                            
                            threading.Thread(target=cache_in_background, daemon=True).start()
                    
                    yield data
            except GeneratorExit:
                logging.info(f"Client {client_ip} disconnected from stream {url}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                finally:
                    try:
                        process.stdout.close()
                        process.stderr.close()
                    except:
                        pass
            except Exception as e:
                logging.error(f'Error in generator for {client_ip}: {str(e)}')
                process.terminate()
                try:
                    process.stdout.close()
                    process.stderr.close()
                except:
                    pass

        response = Response(generate(), content_type='video/mp2t')
        response.headers['Accept-Ranges'] = 'none'
        response.headers['Connection'] = 'keep-alive'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        
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
                    try:
                        process.stdout.close()
                        process.stderr.close()
                    except:
                        pass

        return response

    except Exception as e:
        logging.error(f'Error occurred: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/cache/status', methods=['GET'])
def cache_status():
    """Return cache status and statistics."""
    with STREAM_CACHE_LOCK:
        cached_entries = []
        current_time = time.time()
        for url, entry in STREAM_CACHE.items():
            age_seconds = current_time - entry['timestamp']
            cached_entries.append({
                'url': url,
                'age_seconds': int(age_seconds),
                'expires_in_seconds': int(max(0, CACHE_TTL_SECONDS - age_seconds)),
                'is_valid': age_seconds < CACHE_TTL_SECONDS
            })
        
        return jsonify({
            'total_cached': len(STREAM_CACHE),
            'cache_ttl_seconds': CACHE_TTL_SECONDS,
            'entries': cached_entries
        })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({'status': 'ok', 'service': 'iptv-youtube-proxy'})

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

    # Start background threads
    sync_thread = threading.Thread(
        target=sync_and_regenerate_loop,
        args=(DEFAULT_SYNC_INTERVAL_SECONDS,),
        daemon=True
    )
    sync_thread.start()

    # Start cache refresh thread (will pre-warm immediately if enabled, then refresh periodically)
    cache_thread = threading.Thread(
        target=refresh_stream_cache_loop,
        args=(CACHE_REFRESH_INTERVAL_SECONDS, STARTUP_CACHE_ENABLED),
        daemon=True
    )
    cache_thread.start()

    app.run(host='0.0.0.0', port=DEFAULT_PORT)
