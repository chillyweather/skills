# Jellyfin naming rules

Checked against the Jellyfin documentation and source in September 2026.
Sources: <https://jellyfin.org/docs/general/server/media/shows>, `.../movies`, `.../excluding-directory`,
`.../metadata/nfo`, and `Emby.Server.Implementations/Library/IgnorePatterns.cs`.

## Shows

```text
Frieren - Beyond Journey's End (2023) [tmdbid-209867]/
  tvshow.nfo
  poster.jpg  backdrop.jpg  logo.png
  Season 00/
    Frieren - Beyond Journey's End S00E01.mkv
  Season 01/
    poster.jpg  season.nfo
    Frieren - Beyond Journey's End S01E01.mkv
    Frieren - Beyond Journey's End S01E01.nfo
    Frieren - Beyond Journey's End S01E01.en.ass
    Frieren - Beyond Journey's End S01E01.ru.forced.ass
    Frieren - Beyond Journey's End S01E01.AniLibria.ru.mka
  clips/
    NCOP1.mkv
  _jellyfin-organize/          ignored by Jellyfin because of its .ignore file
    .ignore
    leftovers/
    undo-<time>.json
```

- Season folders are `Season 01`, never `S01` or `SE01`.
  Zero-pad them.
- Episodes carry `SxxEyy`.
  Two episodes in one file: `S01E01-E02`.
  One episode in two files: `S02E03 Part 1`, `S02E03 Part 2`.
- Do not put episodes loose in the series folder next to season folders.
- Specials go in `Season 00`.
  Use `S00Exx` only when TMDB lists that special under that number.
  Otherwise give it a descriptive name, such as `Season 00/Frieren - Recap Film.mkv`.

## Movies

```text
Inception (2010) [tmdbid-27205]/
  Inception (2010) [tmdbid-27205].mkv
  Inception (2010) [tmdbid-27205].ru.srt
  movie.nfo  poster.jpg  backdrop.jpg  logo.png
  trailers/
```

- Each video name must start with the exact folder name.
- Versions: `Inception (2010) [tmdbid-27205] - 2160p.mkv`.
- Parts: `...-part1.mkv`, `...-part2.mkv`.
  `cd`, `dvd`, `part`, `pt`, `disc`, and `disk` all work.
- A standalone anime film is a movie.
  It belongs in the Movies library, not in `Season 00` of the show.

## Provider IDs

- `[tmdbid-123]`, `[tvdbid-123]`, and `[imdbid-tt123]` all work in folder names.
  This skill uses TMDB, because it is Jellyfin's default provider.
- Replace reserved characters `< > : " / \ | ? *`.
  `Title: Sub` becomes `Title - Sub`.

## External subtitles and audio

`<video stem>.<optional title>.<language>.<flags>.<ext>`

- Language: `en`, `ru`, `ja`, or the 3-letter `eng`, `rus`, `jpn`.
- Flags: `default`, `forced` or `foreign`, `sdh` or `cc` or `hi`.
  `hi` alone means Hindi.
- Other text becomes the track title, for example `.AniLibria.ru.mka`.
  Do not put dots inside the title.
- The files must sit next to the video.
  Jellyfin ignores any folder named `subs`, in any case, and everything inside it.

## Extras

Extras folders: `behind the scenes`, `deleted scenes`, `interviews`, `scenes`, `samples`, `shorts`, `featurettes`,
`clips`, `other`, `extras`, `trailers`, `theme-music`, `backdrops`.

The script sends NCOP, NCED, OP, and ED to `clips`.
It sends PV, CM, trailers, teasers, and previews to `trailers`.
It sends menus and bonus material to `extras`.

## Images

Series, season, or movie folder: `poster.jpg` (or `folder`, `cover`), `backdrop.jpg` (or `fanart`), `logo.png`,
`banner.jpg`, `thumb.jpg`.
Episode image: `<episode stem>-thumb.jpg`.

## .nfo files

`tvshow.nfo`, `season.nfo` in the season folder, `<episode stem>.nfo`, and `movie.nfo`.
Local `.nfo` data takes priority over online providers.
Jellyfin still fills fields the file leaves out.
Scene release `.nfo` files are plain text, not XML.
The script moves them to leftovers so Jellyfin does not try to read them.

## What Jellyfin ignores

- Hidden files and folders, starting with `.`.
- `sample.*`, `*.sample.*`, and `sample/`.
- `subs/`, `extrafanart/`, `metadata/`, `@eaDir/`, `#recycle/`, `lost+found/`.
- Any folder that holds a `.ignore` file.

## Anime notes

- Releases often number episodes from the start of the whole show, not the season.
  The script maps these absolute numbers onto TMDB seasons.
- Russian releases often ship dubs in `RUS Sound/[Group]/` and subtitles in `RUS Subs/[Group]/`.
  The script pairs them by episode number and keeps the group name as the track title.
- Loose `Fonts/` folders are not used by Jellyfin.
  Styled `.ass` subtitles render best when their fonts are attached inside the MKV, or when they are installed in the
  server's fallback font folder under Dashboard, Playback.
