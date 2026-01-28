import subprocess
import json
import logging
import socket
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
        # Get stream info with more detailed output
        info_command = ['streamlink', '--json', '--loglevel', 'debug', url]
        info_process = subprocess.Popen(info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info_output, info_error = info_process.communicate()

        if info_process.returncode != 0:
            error_msg = info_error.decode('utf-8', errors='replace')
            logging.error(f'Streamlink error: {error_msg}')
            return jsonify({'error': 'Failed to retrieve stream info', 'details': error_msg}), 500

        # Parse the JSON output
        stream_info = json.loads(info_output.decode('utf-8', errors='replace'))

        # Check if streams are available
        if 'streams' not in stream_info or not stream_info['streams']:
            if 'youtube.com' in url.lower() or 'youtu.be' in url.lower():
                yt_command = ['youtube-dl', '--get-url', '--youtube-skip-dash-manifest', url]
                yt_process = subprocess.Popen(yt_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                yt_url, yt_error = yt_process.communicate()
                
                if yt_process.returncode != 0:
                    logging.error(
                        f"youtube-dl error: {yt_error.decode('utf-8', errors='replace')}"
                    )
                    return jsonify({'error': 'No valid streams found'}), 404
                
                url = yt_url.decode('utf-8', errors='replace').strip()
                info_command = ['streamlink', '--json', url]
                info_process = subprocess.Popen(info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                info_output, info_error = info_process.communicate()
                stream_info = json.loads(info_output.decode('utf-8', errors='replace'))

        best_quality = stream_info['streams'].get('best')
        if not best_quality:
            return jsonify({'error': 'No valid streams found'}), 404

        # Command to run Streamlink
        command = [
            'streamlink',
            url,
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

    app.run(host='0.0.0.0', port=DEFAULT_PORT)
