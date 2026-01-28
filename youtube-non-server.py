import xml.etree.ElementTree as ET
import subprocess
import json
import logging

# Set up logging to show only info and above
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def parse_xml(file_path):
    """Parse the XML file and extract channel information."""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        channels = []
        for channel in root.findall('channel'):
            channel_info = {
                'name': (channel.find('channel-name').text or '').strip() if channel.find('channel-name') is not None else 'Unknown',
                'tvg-id': (channel.find('tvg-id').text or '').strip() if channel.find('tvg-id') is not None else 'Unknown',
                'tvg-name': (channel.find('tvg-name').text or '').strip() if channel.find('tvg-name') is not None else 'Unknown',
                'tvg-logo': (channel.find('tvg-logo').text or '').strip() if channel.find('tvg-logo') is not None else '',
                'group-title': (channel.find('group-title').text or '').strip() if channel.find('group-title') is not None else 'General',
                'youtube-url': (channel.find('youtube-url').text or '').strip() if channel.find('youtube-url') is not None else ''
            }
            if channel_info['youtube-url']:
                channels.append(channel_info)
            else:
                logging.warning(f"Skipping channel '{channel_info['name']}' due to missing YouTube URL.")
        return channels
    except Exception as e:
        logging.error(f'Failed to parse XML file: {str(e)}')
        return None

def get_stream_url(youtube_url):
    """Use Streamlink to extract the best stream URL."""
    try:
        info_command = ['streamlink', '--json', youtube_url]
        info_process = subprocess.Popen(info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info_output, info_error = info_process.communicate()

        if info_process.returncode != 0:
            logging.error(f'Streamlink error for {youtube_url}: {info_error.decode().strip()}')
            return None

        try:
            stream_info = json.loads(info_output)
            return stream_info['streams'].get('best', {}).get('url')
        except json.JSONDecodeError:
            logging.error(f'Failed to parse Streamlink output for {youtube_url}.')
            return None
    except Exception as e:
        logging.error(f'Error retrieving stream URL for {youtube_url}: {str(e)}')
        return None

def generate_m3u(channels, output_file):
    """Generate an M3U playlist from channel information."""
    try:
        with open(output_file, 'w', encoding='utf-8') as m3u:
            m3u.write('#EXTM3U\n')
            for channel in channels:
                stream_url = get_stream_url(channel['youtube-url'])
                if stream_url:
                    m3u.write(f'#EXTINF:-1 tvg-id="{channel["tvg-id"]}" tvg-name="{channel["tvg-name"]}" '
                              f'tvg-logo="{channel["tvg-logo"]}" group-title="{channel["group-title"]}",'
                              f'{channel["name"]}\n')
                    m3u.write(f'{stream_url}\n')
                    logging.info(f"{channel['name']} exported successfully")
    except Exception as e:
        logging.error(f'Failed to generate M3U file: {str(e)}')

if __name__ == '__main__':
    xml_file = 'youtubelinks.xml'
    output_m3u = 'youtube_non_server.m3u'

    channels = parse_xml(xml_file)
    if channels:
        generate_m3u(channels, output_m3u)
    else:
        logging.error('No channels found. Exiting.')