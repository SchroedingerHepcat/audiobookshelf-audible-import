# audiobookshelf-audible-import
Imports Audible audiobooks into an [audiobookshelf](https://www.audiobookshelf.org/) compatible library directory structure.

Currently, this represents my work to import my Audible library into my [audiobookshelf](https://www.audiobookshelf.org/) server.  It is currently in a very rough state; I may or may not do additional work to make it more useful to others, but I wanted to make it available for others so they didn't need to start from scratch.

## Dependencies

This work depends on the work provided by the following python dependencies (thank you to to their developers!):
* [audible api](https://github.com/mkb79/Audible)
* [audible cli](https://github.com/mkb79/audible-cli)
* [ffmpeg-python](https://github.com/kkroening/ffmpeg-python)

## Running
You will need to set up an audible authentication file to begin.  Using the `audible cli` interface, you can run `audible quickstart` or `audible-quickstart` to establish this.
You will need to edit the config structure in the audible-audiobookshelf-import.py file to point to both your audiobookshelf library as well as an audible download location for the audible files.
Once that has been established, you can run `audiobookshelf.py`.
