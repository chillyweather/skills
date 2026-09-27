# Tags and layout for Navidrome

Checked against the Navidrome documentation and `resources/mappings.yaml` in September 2026.

## How Navidrome groups music

- Tags only.
  Folder and file names are ignored.
- An album is identified by `musicbrainz_albumid` when present.
  Otherwise it uses album artist, album, album version, and release date.
  Two editions of one album therefore stay separate when each carries its own MusicBrainz release ID.
- Every track of an album needs the same `ALBUM`, `ALBUMARTIST`, and `MUSICBRAINZ_ALBUMID`.
  One stray value splits the album.
- A compilation needs `ALBUMARTIST=Various Artists` and `COMPILATION=1`.

## Tags the script writes

Names are Vorbis comment names, as MusicBrainz Picard writes them.
The script writes the matching ID3v2.4 frame, MP4 atom, or APEv2 key for other formats.

| Tag | Value |
| --- | --- |
| `TITLE` | Track title from the release |
| `ARTIST` | Track artist credit as displayed, such as `Alice feat. Bob` |
| `ARTISTS` | Each track artist as its own value |
| `ARTISTSORT` | Sort form of the credit |
| `ALBUM` | Release title |
| `ALBUMARTIST`, `ALBUMARTISTS`, `ALBUMARTISTSORT` | The same for the release artist |
| `TRACKNUMBER`, `TRACKTOTAL` | Position on the disc, and track count of the disc |
| `DISCNUMBER`, `DISCTOTAL`, `DISCSUBTITLE` | Disc position, disc count, disc title |
| `DATE`, `RELEASEDATE` | Release date of this edition |
| `ORIGINALDATE` | First release date of the album, any edition |
| `GENRE` | Up to three top genres of the release group on MusicBrainz |
| `LABEL`, `CATALOGNUMBER`, `BARCODE`, `MEDIA` | Edition details |
| `RELEASETYPE`, `RELEASESTATUS`, `RELEASECOUNTRY`, `SCRIPT` | Release details |
| `ISRC` | Recording codes |
| `COMPILATION` | `1` for Various Artists releases |
| `MUSICBRAINZ_TRACKID` | Recording ID |
| `MUSICBRAINZ_RELEASETRACKID`, `MUSICBRAINZ_ALBUMID`, `MUSICBRAINZ_RELEASEGROUPID` | Track, release, release group IDs |
| `MUSICBRAINZ_ARTISTID`, `MUSICBRAINZ_ALBUMARTISTID` | Artist IDs, one value per artist |

Other tags, such as comments, lyrics, composer, and ReplayGain, stay as they are.
The script also removes old duplicate spellings that Navidrome would read, such as `ALBUM ARTIST`, `YEAR`,
`TOTALTRACKS`, and ID3v1 tags.

## Layout

```text
<dest>/
  Кино/
    artist.jpg
    Группа крови (1988)/
      01 - Группа крови.flac
      ...
      cover.jpg
      Scans/                    booklet and other images from the download
      _navidrome-organize/      ignored by Navidrome because of .ndignore
        .ndignore
        leftovers/              logs, cue sheets, text files, the original CUE image after a split
        undo-<time>.json
        tags-<time>.pickle      the old tags, for undo
```

- The year in the folder name is the original year of the album, not of the edition.
- Multi-disc releases get `Disc 1/`, `Disc 2/`.
- Various Artists files are named `NN - Artist - Title`.
- `/` in names becomes `_`, so `AC/DC` becomes `AC_DC` in paths.
  The tags keep `AC/DC`.

## Artwork Navidrome reads

- Album: `cover.*`, `folder.*`, or `front.*` in the album folder, then the embedded image.
- Disc: `disc*.*` or `cd*.*` in the disc folder.
- Artist: `artist.*` in the artist folder, or in an album folder.

## Ignoring files

An empty `.ndignore` file makes Navidrome skip its folder and everything below it.
A non-empty one holds `.gitignore` patterns.
