# youtube-to-m3u
Play YouTube live streams in any player

## ⚡ Recent Performance Optimizations

**Version 2.0** includes major performance improvements:
- **4-5x faster** channel switching (0.5-1 second vs 2-5 seconds)
- Pre-warming cache on startup (no first-time delays)
- Optimized streamlink usage with `--stream-url`
- Upgraded to yt-dlp (2-3x faster than youtube-dl)
- Parallel HLS segment downloads
- Optional direct redirect mode for instant playback

See [QUICKSTART.md](QUICKSTART.md) for setup instructions and [DELAY_ANALYSIS.md](DELAY_ANALYSIS.md) for technical details.

## Important Note
The m3u/extracted m3u8 links will only work on machines that have the same public IP address (on the same local network) as the machine that extracted them. To play on a client that has a different public IP (on a different network) use a non flask version and load the m3u into a m3u proxy such as threadfin to restream

## Choose script option
youtube-live.py - Uses a flask server to automatically pull the actuall stream link. Server needs to be running all the time for m3u to work. Best for always working stream<br>
<br>
youtube-non-server.py - Pulls stream link into m3u but script will have to manually run (or cron job) every few hours as the stream links will expire <br>
<br>
youtube_non_stream_link.py - Same as youtube-non-server.py but doesn't require streamlink - only use if you are unable to install streamlink as if anything changes youtube side the script will need updating instead of just updating streamlink

## Requirements
### All Versions
python - must be 3.10 or higher (3.8 or lower is not supported by streamlink) <br>
requests (can be installed by typing ```pip install requests``` at cmd/terminal window) <br>

### All Versions except youtube_non_stream_link.py
install [streamlink](https://streamlink.github.io/install.html) and make it available at path

### youtube-live.py only <br>
flask (can be installed by typing ```pip install flask``` at cmd/terminal window) <br>
**yt-dlp** (can be installed by typing ```pip install yt-dlp``` or ```brew install yt-dlp```) - **NEW: Faster than youtube-dl** <br>
youtubelive.m3u

### youtube-non-server.py and youtube_non_stream_link.py<br>
youtubelinks.xml

## Quick Install
```bash
# Install all dependencies
pip install -r requirements.txt

# Or install manually
pip install flask requests streamlink yt-dlp
```

## Verify streamlink install
To test streamlink install type in a new cmd/terminal window
```
streamlink --version
```
The output should be
streamlink "version number" eg 8.1.2 <br>
If it says unknown command/'streamlink' is not recognized as an internal or external command,
operable program or batch file. <br>
Then you need to make sure you have installed streamlink to path/environmental variables

## How To Use youtube-live.py

### Quick Start (Recommended)
1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure your channels in `youtubelinks.xml` (see format below)

3. Run the server:
```bash
python3 youtube-live.py
```

4. Wait 2-3 minutes for cache pre-warming to complete

5. Load `youtubelive.m3u` in your IPTV player!

### Testing Performance
```bash
# Check if optimizations are working
python3 test_performance.py

# Check cache status
curl http://192.168.1.16:6095/cache/status

# Check server health
curl http://192.168.1.16:6095/health
```

### Manual Configuration
Open youtubelive.m3u <br>
Change the ip address in the streamlink to the ip address of the machine running the script <br>
You can also change the port but if you do this you must change the port to match at the bottom of youtube-live.py <br>
<br>
To add other live streams just add into m3u in the following format 

```
#EXTINF:-1 tvg-name="Channel Name" tvg-id="24.7.Dummy.us" tvg-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/5/54/YouTube_dark_logo_2017.svg/2560px-YouTube_dark_logo_2017.svg.png" group-title="YouTube",Channel Name
http://192.168.1.123:6095/stream?url=https://www.youtube.com/@ChannelName/live
```

Or if the channel has multiple live streams you can use the /watch? link however these links will change if the channel stops and restarts broadcast <br>
<br>
You can change tvg-name tvg-logo group-title and channel name and if you want to link to an epg change tvg-id to match your epgs tvg-id for that channel <br>
(The two sample streams link to the epg from epgshare01.online UK and USA epgs) <br>
<br>
Run the python script <br>
python youtube-live.py or python3 youtube-live.py if you have the old python2 installed <br>
<br>
Script must be running for the m3u to work

### 🚀 Advanced: Redirect Mode (Fastest)
For compatible IPTV players, use redirect mode for instant playback:

```
# Add &redirect=true to stream URLs
http://192.168.1.123:6095/stream?url=https://www.youtube.com/@ChannelName/live&redirect=true
```

**Benefits**: Eliminates proxy overhead, reduces server load, lower latency

**Compatible with**: VLC, MPV, most modern IPTV apps

## How To Use youtube-non-server.py or youtube_non_stream_link.py
Open youtubelinks.xml in a code text editor eg notepad++ <br>
Add in your channel details for your youtube stream in the following format

```
<channel>
        <channel-name>ABC News</channel-name>
        <tvg-id>ABCNEWS.us</tvg-id>
        <tvg-name>ABC News</tvg-name>
        <tvg-logo>https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/abc-news-light-us.png?raw=true</tvg-logo>
        <group-title>News</group-title>
        <youtube-url>https://www.youtube.com/@abcnews/live</youtube-url>
    </channel>
```

channel-name = name of channel <br>
tvg-id = epg tag which matches tvg-id in your epg (you can enter anything here if you don't have an epg or leave blank) <br>
tvg-name = name of channel <br>
tvg-logo = direct link to channel logo png <br>
group-title = group you want channel to appear in <br>
youtube-url = url to youtube live stream - can be @channelname/live or /watch? <br>
<br>
Run the python script <br>
python youtube-non-server.py or python3 youtube-non-server.py if you have the old python2 installed <br>
<br>
As the stream links will expire you will need to setup a cron job/scheduled task or manually run the script every few hours <br>
To have the stream urls automatically be pulled use the flask version <br>
<br>

## 📚 Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Fast setup guide with step-by-step instructions
- **[DELAY_ANALYSIS.md](DELAY_ANALYSIS.md)** - Why streams were slow and how we fixed it
- **[PERFORMANCE_OPTIMIZATION.md](PERFORMANCE_OPTIMIZATION.md)** - Advanced optimization techniques
- **test_performance.py** - Test script to verify performance improvements

## API Endpoints

### Stream Endpoint
```
GET /stream?url=<youtube_url>&redirect=<true|false>
```
- `url`: YouTube live stream URL (URL-encoded)
- `redirect`: Optional, set to `true` for direct redirect mode

### Cache Status
```
GET /cache/status
```
Returns cache statistics and status of all cached streams

### Health Check
```
GET /health
```
Returns server health status

## Configuration

Edit these constants in `youtube-live.py`:

```python
DEFAULT_PORT = 6095  # Server port
CACHE_TTL_SECONDS = 3600  # Cache lifetime (1 hour)
CACHE_REFRESH_INTERVAL_SECONDS = 3600  # Cache refresh interval (1 hour)
STARTUP_CACHE_ENABLED = True  # Pre-warm cache on startup
```

## Troubleshooting

### Streams are slow
1. Check if yt-dlp is installed: `yt-dlp --version`
2. Run performance test: `python3 test_performance.py`
3. Check cache status: `curl http://192.168.1.16:6095/cache/status`
4. See [DELAY_ANALYSIS.md](DELAY_ANALYSIS.md) for detailed troubleshooting

### Cache not working
1. Check server logs for "Pre-warming stream cache"
2. Wait 2-3 minutes after server start
3. Verify streamlink works: `streamlink --stream-url "https://www.youtube.com/@aajtak/live" best`

### Server won't start
1. Check if port is in use: `lsof -i :6095`
2. Kill existing process: `kill -9 <PID>`
3. Check Python version: `python3 --version` (must be 3.10+)

## Performance Benchmarks

| Scenario | Before | After | Improvement |
|----------|--------|-------|-------------|
| First stream (cold) | 5-8 sec | 0.5-1 sec | **85% faster** |
| Cached stream | 2-4 sec | 0.5-1 sec | **75% faster** |
| With redirect mode | N/A | <0.1 sec | **Instant** |

## License

MIT License - Feel free to use and modify
