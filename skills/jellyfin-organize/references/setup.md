# Setup

The script reads `~/.config/jellyfin-organize/config.env`.
Environment variables with the same names override the file.
Keep the file out of every git repository.

```bash
mkdir -p ~/.config/jellyfin-organize
cp <this skill's directory>/assets/config.env.example ~/.config/jellyfin-organize/config.env
chmod 600 ~/.config/jellyfin-organize/config.env
```

Then fill in the values.

## TMDB (required)

1. Make a free account at <https://www.themoviedb.org/signup>.
2. Open <https://www.themoviedb.org/settings/api> and request an API key for personal use.
3. Copy the "API Key" into `TMDB_API_KEY`, or the "API Read Access Token" into `TMDB_READ_TOKEN`.
   One of the two is enough.

## OpenSubtitles (for subtitle downloads)

1. Make a free account at <https://www.opensubtitles.com>.
2. Open <https://www.opensubtitles.com/en/consumers> and create an API consumer.
   Its name is the app name.
3. Copy the API key into `OPENSUBTITLES_API_KEY`.
4. Put the account's username and password into `OPENSUBTITLES_USERNAME` and `OPENSUBTITLES_PASSWORD`.
   Without them the limit is 5 downloads a day per IP address.
   A free account allows about 20 a day, and VIP allows 1000.

Check the result with `jf.py config`.
