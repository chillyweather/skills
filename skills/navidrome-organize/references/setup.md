# Setup

MusicBrainz and the Cover Art Archive need no account or key.
The script works without any setup.

The optional settings live in `~/.config/navidrome-organize/config.env`.
Environment variables with the same names override the file.

```bash
mkdir -p ~/.config/navidrome-organize
cp <this skill's directory>/assets/config.env.example ~/.config/navidrome-organize/config.env
chmod 600 ~/.config/navidrome-organize/config.env
```

## AcoustID (identify files without tags)

1. Install the fingerprint tool: `brew install chromaprint`.
   It provides `fpcalc`.
2. Sign in at <https://acoustid.org/login> and register an application at <https://acoustid.org/new-application>.
3. Copy the application's API key into `ACOUSTID_API_KEY`.

## fanart.tv (artist images)

1. Make a free account at <https://fanart.tv>.
2. Open <https://fanart.tv/get-an-api-key/> and copy your personal API key into `FANARTTV_API_KEY`.

## Other settings

- `COVER_MAX`: largest side of `cover.jpg` in pixels, default 1500.
- `EMBED_MAX`: largest side of the embedded cover, default 1000.
- `EMBED_COVER`: `1` to embed the cover in every file, `0` to keep only `cover.jpg`.

Check the result with `nd.py config`.
