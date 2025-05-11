#! /usr/bin/env python3

from datetime import datetime
import json
import logging
import os
#import os.path
import pathlib
import shutil
import sqlite3
import subprocess
import time
import tomllib
import urllib.parse
from urllib.parse import urljoin

from audio_book_shelf import AudioBookShelf
import audible
import audible_cli
import ffmpeg
import requests

logger = logging.getLogger(__name__)

# Haven't figured out how to do podcasts/episodes yet
asin_to_skip = ['B0CK8RZ2GR'
               ,'B0BR2QK8M7'
               ,'B0BJ7BV4PH'
               ,'B08JCLTS2P'
               ]

config_filename = os.getenv("AUDIBLE_AUDIOBOOKSHELF_CONFIG_FILE")
if config_filename:
    config_file = pathlib.Path(config_filename)
else:
    config_file = ( pathlib.Path.home()
                  / ".config"
                  / "audiobookshelf"
                  / "config.toml"
                  )
with config_file.open("rb") as f:
    config = tomllib.load(f)

shelf = AudioBookShelf(config=config["audiobookshelf"])

#config = {}
#config['db'] = pathlib.Path.home() / '.audible' / 'audiobookshelf.db'
#config['quality'] = 'best'
#config['audible_download_dir'] = os.path.expanduser('~/data/Audiobooks2/Audible/cli')
#config['audible_download_dir'] = ( pathlib.Path.home()
#                                 / 'data'
#                                 / 'Audiobooks2'
#                                 / 'Audible'
#                                 / 'cli'
#                                 )
#config['audible_auth_file'] = os.path.expanduser('~/.audible/audible.json')
#config['audible_auth_file'] = pathlib.Path.home() / '.audible' / 'audible.json'
#config['audiobookshelf_dir'] = pathlib.Path.home() / 'data' / 'Audiobooks'
#config['audiobookshelf_podcast_dir'] = ( pathlib.Path.home()
#                                       / 'data'
#                                       / 'Audiobooks'
#                                       )
#config['activation_bytes'] = '2b6d2001'
#config['tmp_dir'] = '/tmp'

RELEASE_DATE_FORMAT = "%Y-%m-%d"



class ImportDatabase:
    '''
    Tracks what files have already been imported into audiobookshelf to prevent
    constant redownloading, reconverting, and/or reimporting of files
    '''
    def __init__(self, db_file):
        self.con = sqlite3.connect(db_file)
        self.cur = self.con.cursor()
        #self.setup_database(config['db'])
        self.setup_database(config['database']['location'])

    def is_book_already_imported(self, asin):
        '''
        Check whether specified book has already been imported
        '''
        res = self.cur.execute("SELECT asin FROM books WHERE asin = ?", (asin,))
        return len(res.fetchall()) > 0

    def is_podcast_episode_already_imported(self, asin):
        '''
        Check whether specified book has already been imported
        '''
        res = self.cur.execute("SELECT asin FROM podcast_episodes "
                               "WHERE asin = ?"
                              ,(asin,)
                              )
        return len(res.fetchall()) > 0

    def record_book_as_imported(self, asin, title, abs_path, abs_dir):
        '''
        Record a book as imported
        '''
        # In case abs_dir or abs_path are strings, convert to a pathlib.Paths
        abs_dir = pathlib.Path(abs_dir)
        abs_path = pathlib.Path(abs_path)

        self.cur.execute('INSERT INTO books (asin, title, location) '
                         'values (?, ?, ?)'
                        ,(asin, title, abs_path.relative_to(abs_dir).as_posix())
                        )
        self.con.commit()

    def record_episode_as_imported(self, asin, title, abs_path, abs_dir):
        '''
        Record a book as imported
        '''
        # In case abs_dir or abs_path are strings, convert to a pathlib.Paths
        abs_dir = pathlib.Path(abs_dir)
        abs_path = pathlib.Path(abs_path)

        self.cur.execute('INSERT INTO podcast_episodes (asin, title, location) '
                         'values (?, ?, ?)'
                        ,(asin, title, abs_path.relative_to(abs_dir).as_posix())
                        )
        self.con.commit()

    def setup_database(self, filename):
        '''
        Set up database tables
        '''
        con = sqlite3.connect(filename)
        cur = con.cursor()
        cur.execute('CREATE TABLE if not exists books(asin, title, location)')
        cur.execute('CREATE TABLE if not exists podcast_episodes(asin, title, '
                    'location)'
                   )


def extract_chapters(input_file):
    '''
    Extract chapter list and timings from a single AAX or AAXC file.

    Args:
        input_file (str or Path): Input file path.

    Returns:
        A list of dictionaries with 'start_time', 'end_time', and 'title' for each chapter.
    '''
    logger = logging.getLogger(__name__)
    chapters = []

    try:
        input_file = str(pathlib.Path(input_file))
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_chapters',
            '-i', input_file
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE
                               ,stderr=subprocess.PIPE
                               ,text=True
                               ,check=True
                               )
        probe_data = json.loads(result.stdout)
        chapter_list = probe_data.get('chapters', [])

        for idx, chapter in enumerate(chapter_list):
            start_time = float(chapter['start_time'])
            end_time = float(chapter['end_time'])
            title = chapter.get('tags', {}).get('title', f"Chapter {idx + 1}")
            chapters.append({
                'start_time': start_time,
                'end_time': end_time,
                'title': title
            })

    except subprocess.CalledProcessError as e:
        logger.error("Failed to extract chapters from %s: %s", input_file, e.stderr)

    return chapters


def get_audible_library(auth=None):
    logger = logging.getLogger(__name__)
    if not auth:
        auth = audible.Authenticator.from_file(config['audible']['auth_file'])
    with audible.Client(auth=auth) as client:
        library = []
        page = 1
        while True:
            logger.info(f"...Retrieving library index page {page}...")
            books = client.get("1.0/library"
                              ,num_results=100
                              ,page=page
                              ,response_groups=("contributors, media, "
                                                "product_attrs, product_desc, "
                                                "product_extended_attrs, "
                                                "sample, series, "
                                                "ws4v, origin, relationships, "
                                                "categories, "
                                                "category_ladders, "
                                                "origin_asin"
                                               )
                              )
            library.extend(books['items'])
            if len(books['items']) == 0:
                break
            page += 1
        return library


def get_audible_product(asin, auth=None):
    logger = logging.getLogger(__name__)
    if not auth:
        auth = audible.Authenticator.from_file(config['audible']['auth_file'])
    with audible.Client(auth=auth) as client:
        product = client.get(f"1.0/catalog/products/{asin}"
                            ,response_groups=("contributors, media, "
                                              "product_attrs, product_desc, "
                                              "product_extended_attrs, "
                                              "sample, series, "
                                              "ws4v, relationships, "
                                              "categories, "
                                              "category_ladders"
                                             )
                           )
        if 'product' in product:
            return product['product']
        return product


def download_book_as_aax(asin, quality, download_dir, filename_mode='asin_ascii'):
    logger = logging.getLogger(__name__)
    os.chdir(download_dir)
    audible_cli.cli.cli(['download'
                        ,'--asin', asin
                        ,'--quality', quality
                        ,'--output-dir', download_dir
                        ,'--filename-mode', filename_mode
                        ,'--aax'
                        ]
                       ,standalone_mode=False
                       )

    # Get downloaded filename
    # Check for aax file
    aax_path = list(pathlib.Path(config['files']['audible_download_dir'])
                   .glob(f"{asin}*.aax")
                   )
    return aax_path


def download_product_as_aax(asin
                           ,quality
                           ,download_dir
                           ,filename_mode='asin_ascii'
                           ,book=None
                           ):
    os.chdir(download_dir)
    audible_cli.cli.cli(['download'
                        ,'--asin', asin
                        ,'--quality', quality
                        ,'--output-dir', download_dir
                        ,'--filename-mode', filename_mode
                        ,'--aax'
                        ]
                       ,standalone_mode=False
                       )

    # Get downloaded filename
    # Check for aax file
    aax_paths = [p.resolve() for p in download_dir.glob(f'{asin}*.aax')]
    if book and not aax_paths and book.get('relationships'):
        parts = [rel for rel in book['relationships']
                if rel['relationship_type'] == 'component'
                ]
        sorted_parts = sorted(parts, key=lambda x: x['sort'])
        sorted_asins = [p['asin'] for p in sorted_parts]
        aax_paths = []
        for a in sorted_asins:
            part_aax_paths = [   p.resolve()
                             for p
                             in download_dir.glob(f'{a}*.aax')
                             ]
            if part_aax_paths:
                aax_paths.extend(part_aax_paths)
    return aax_paths


def download_product_as_aaxc(asin
                            ,quality
                            ,download_dir
                            ,filename_mode='asin_ascii'
                            ):
    os.chdir(download_dir)
    audible_cli.cli.cli(['download'
                        ,'--asin', asin
                        ,'--quality', quality
                        ,'--output-dir', download_dir
                        ,'--filename-mode', filename_mode
                        ,'--aaxc'
                        ]
                       ,standalone_mode=False
                       )

    # Check for aaxc file
    aaxc_paths = [p.resolve() for p in  download_dir.glob(f'{asin}*.aaxc')]
    voucher_paths = [p.resolve() for p in download_dir.glob(f'{asin}*.voucher')]
    return (aaxc_paths, voucher_paths)


def download_podcast_episode(asin
                            ,download_dir
                            ,quality='best'
                            ,filename_mode='asin_ascii'
                            ):
    #TODO
    episode_m4b_path = None
    aax_paths = download_product_as_aax(asin=asin
                                       ,download_dir=download_dir
                                       ,quality=quality
                                       ,filename_mode=filename_mode
                                       )
    if aax_paths:
        episode_m4b_path = convert_aax_to_m4b(aax_paths)
    else:
        aaxc_paths, voucher_paths = download_product_as_aaxc(
                                         asin=asin
                                        ,download_dir=download_dir
                                        ,quality=quality
                                        ,filename_mode=filename_mode
                                        )
        if aaxc_paths and voucher_paths:
            episode_m4b_path = convert_aaxc_to_m4b(aaxc_paths, voucher_paths)

    return episode_m4b_path


def convert_aax_to_m4b(aax_paths, output_dir=None, book=None):
    if not output_dir:
        output_dir = config['files']['tmp_dir']
    output_dir = pathlib.Path(output_dir)
    m4b_paths = []
    for aax_path in aax_paths:
        m4b_file = (output_dir / aax_path.name).with_suffix(".m4b")
        (ffmpeg.input(aax_path.as_posix()
                     ,activation_bytes=config['audible']['activation_bytes']
                     )
               .output(m4b_file.as_posix(), codec='copy')
               .run()
        )
        m4b_paths.append(m4b_file)
    return m4b_paths


def convert_aaxc_to_m4b(aaxc_paths, voucher_paths, output_dir=None):
    if not output_dir:
        output_dir = config['files']['tmp_dir']
    output_dir = pathlib.Path(output_dir)
    m4b_files = []
    for aaxc_path, voucher_path in zip(aaxc_paths, voucher_paths):
        m4b_file = (output_dir / aaxc_path.name).with_suffix(".m4b")

        # Extract license
        voucher = json.load(voucher_path.open('r'))
        voucker_key = voucher['content_license']['license_response']['key']
        voucher_iv = voucher['content_license']['license_response']['iv']

        # Convert to m4b
        (ffmpeg.input(aaxc_path.as_posix()
                     ,activation_bytes=config['audible']['activation_bytes']
                     ,audible_key=voucker_key
                     ,audible_iv=voucher_iv
                     )
               .output(m4b_file.as_posix(), codec='copy')
               .run()
        )
        m4b_files.append(m4b_file)
    return m4b_files


def add_podcast(podcast, download_dir, import_db, auth=None):
    logger = logging.getLogger(__name__)
    seasons = [child for child in podcast['relationships']
               if     child['relationship_to_product'] == 'child'
                  and child['relationship_type'] == 'season'
              ]
    if seasons:
        # Podcast is organized into seasons, so download by season
        for season in seasons:
            # Download season information
            season_info = get_audible_product(season['asin'], auth=auth)
            episode_asins = [child['asin'] for child
                             in season_info['relationships']
                             if     child['relationship_to_product'] == 'child'
                                and child['relationship_type'] == 'episode'
                            ]
            add_podcast_episodes(episode_asins=episode_asins
                                ,import_db=import_db
                                ,download_dir=download_dir
                                ,podcast_info=podcast
                                ,season_info=season
                                ,auth=auth
                                )
    else:
        # Podcase is not organized into seasons, so download by episodes
        episode_asins = [child['asin'] for child
                         in season_info['relationships']
                         if     child['relationship_to_product'] == 'child'
                            and child['relationship_type'] == 'episode'
                        ]
        add_podcast_episodes(episode_asins=episode_asins
                            ,import_db=import_db
                            ,download_dir=download_dir
                            ,podcast_info=podcast
                            )


def add_podcast_episodes(episode_asins
                        ,import_db
                        ,download_dir
                        ,podcast_info
                        ,season_info=None
                        ,auth=None
                        ):
    logger = logging.getLogger(__name__)
    for asin in episode_asins:
        if import_db.is_podcast_episode_already_imported(asin):
            logger.info("Episode is already imported: %s  %s: %s"
                       ,podcast_info['asin']
                       ,podcast_info['title']
                       ,asin
                       )
            continue
        episode_info = get_audible_product(asin=asin, auth=auth)
        episode_path = download_podcast_episode(asin, download_dir)
        if episode_path:
            title, abs_path = import_episode_into_audiobookshelf(
                 m4b_file=episode_path
                ,podcast_info=podcast_info['title']
                ,season_info=season_info
                ,episode_info=episode_info
                ,abs_dir=config['audiobookshelf_ppodcast_dir']
            )
        else:
            logger.warning("Episode not imported: %s  %s: %s  %s"
                          ,podcast_info['asin']
                          ,podcast_info['title']
                          ,asin
                          ,episode_info['title']
                          )
            continue

        # Record episode as having been added to the library
        import_db.record_episode_as_imported(
             asin=asin
            ,title=title
            ,abs_path=abs_path
            ,abs_dir=config['audiobookshelf']['podcast_dir']
        )


def import_episode_into_audiobookshelf(m4b_file
                                      ,podcast_info
                                      ,season_info
                                      ,episode_info
                                      ,abs_dir
                                      ):
    # In case abs_dir is a string, convert it to a pathlib.Path
    abs_dir = pathlib.Path(abs_dir)

    podcast_title = podcast_info['title']
    podcast_dir = abs_dir / podcast_title
    podcast_dir.mkdir(parents=True, exist_ok=True)
    episode_file = shutil.move(m4b_file, podcast_dir /m4b_file.name)
    return episode_file.name, episode_file


def import_audiobook_into_audiobookshelf(m4b_files, book_info, abs_dir):
    # In case abs_dir is a string, convert it to a pathlib.Path
    abs_dir = pathlib.Path(abs_dir)

    author = ', '.join([a['name'] for a in book_info['authors']])
    if book_info['series']:
        series = book_info['series'][0]['title']
        series_position = book_info['series'][0]['sequence']
    else:
        series = ""
        series_position = ""
    title = book_info['title']
    subtitle = book_info['subtitle']
    if book_info['narrators']:
        narrators = ', '.join([n['name'] for n in book_info['narrators']])
    else:
        narrators = ''

    book_dir = abs_dir / author
    if series:
        book_dir = book_dir / series
        if series_position:
            title = series_position + ' - ' + title
    if subtitle:
        title = title + ' - ' + subtitle
    if narrators:
        title_len = len(title) + 3
        if title_len + len(narrators) > 255:
            narrators = ""
            for n in book_info['narrators']:
                if title_len + len(narrators) + len(n['name']) < 254:
                    if narrators == "":
                        narrators = n['name']
                    else:
                        narrators = narrators + ', ' + n['name']
        title = title + ' {' + narrators + '}'

    book_dir = book_dir / title
    book_dir.mkdir(parents=True, exist_ok=True)
    for m4b_file in m4b_files:
        abs_path = shutil.move(m4b_file, book_dir / m4b_file.name)
    return title, abs_path


def is_product_released(product_info):
    date_format = "%Y-%m-%d"
    release_date = datetime.strptime(product_info['release_date'], date_format)
    return release_date <= datetime.now()


def add_book(book, db, download_dir, auth=None):
    logger = logging.getLogger(__name__)
    # Download book as aax
    logger.info('Trying to download as aax: %s', book['asin'])
    aax_paths = download_product_as_aax(
                     asin=book['asin']
                    ,quality=config['audible']['quality']
                    ,download_dir=download_dir
                    ,book=book
                    )
    if len(aax_paths) > 0:
        tmp_m4b_files = convert_aax_to_m4b(aax_paths
                                          ,output_dir=config['files']['tmp_dir']
                                          ,book=book
                                          )
    else:
        # Download book as aaxc
        logger.info('Trying to download as aaxc: %s', book['asin'])
        aaxc_paths, voucher_paths = download_product_as_aaxc(
             book['asin']
             ,quality=config['audible']['quality']
             ,download_dir=pathlib.Path(config['files']['audible_download_dir'])
             ,filename_mode='asin_ascii'
        )

        # Check for aaxc file
        if len(aaxc_paths) > 0 and len(voucher_paths) > 0:
            tmp_m4b_files = convert_aaxc_to_m4b(aaxc_paths=aaxc_paths
                                               ,voucher_paths=voucher_paths
                                               ,output_dir=config['files']['tmp_dir']
                                               )

        else:
            logger.warning("No aax or aaxc file for this title: ASIN: %s "
                           "Title: %s"
                          ,book['asin']
                          ,book['title']
                          )
            return

    # Put it in place in the audiobookshelf
    title, abs_path = import_audiobook_into_audiobookshelf(
         m4b_files=tmp_m4b_files
        ,book_info=book
        ,abs_dir=pathlib.Path(config['audiobookshelf']['audiobooks_dir'])
    )

    # Add chapters to audiobookshelf
    library_id = shelf.get_book_library_id()
    shelf.trigger_library_rescan(library_id)
    while not (book_id := shelf.get_item_id_for_folder(library_id
                                                      ,abs_path.parent
                                                      )
              ):
        time.sleep(1)
    shelf.update_item_asin(book_id, book['asin'])
    chapters = shelf.fetch_chapters(book['asin'])
    shelf.update_item_chapters(book_id, chapters)

    # Record it as having been added to the library
    db.record_book_as_imported(asin=book['asin']
                              ,title=title
                              ,abs_path=abs_path
                              ,abs_dir=config['audiobookshelf']['audiobooks_dir']
                              )


def main():
    logger = logging.getLogger(__name__)
    logger.info("Getting library...")
    auth = audible.Authenticator.from_file(config['audible']['auth_file'])
    library = get_audible_library(auth)
    logger.info("Connecting to db...")
    db = ImportDatabase(config['database']['location'])
    logger.info("Handling library...")
    for book in library:
        # Check if book has already been downloaded and added to library
        logger.info("ASIN: %s    Title: %s"
                   ,book['asin']
                   ,book['title']
                   )
        if book['asin'] in asin_to_skip:
            logger.info('Book is on the skip list: %s  %s'
                       ,book['asin']
                       ,book['title']
                        )
            continue

        # Skip periodicals for now -- TODO
        if book['content_delivery_type'] == 'Periodical':
            logger.warning("Skipping because it is of content delivery type "
                           "Periodical: %s  %s"
                          ,book['asin']
                          ,book['title']
                          )
            continue
        # Skip podcasts for now -- TODO
        elif book['content_delivery_type'] == 'PodcastParent':
            logger.warning("Skipping because it is of content delivery type "
                           "PodcastParent: %s  %s"
                          ,book['asin']
                          ,book['title']
                          )
            continue
            #add_podcast(podcast=book
            #           ,download_dir=config['files']['audible_download_dir']
            #           ,import_db=db
            #           ,auth=auth
            #           )
        elif (  book['content_delivery_type'] == 'SinglePartBook'
             or book['content_delivery_type'] == 'MultiPartBook'
             ):
            if db.is_book_already_imported(book['asin']):
                # This book has already been downloaded and added to the library so
                # move on to the next book in the list
                logger.info('Book is already imported: %s  %s'
                           ,book['asin']
                           ,book['title']
                            )
                continue

            # Check if book is published yet
            if not is_product_released(book):
                logger.warning('Book is not yet released: %s %s - Release date: %s'
                              ,book['asin']
                              ,book['title']
                              ,book['release_date']
                              )
                continue
            add_book(book=book
                    ,db=db
                    ,download_dir=pathlib.Path(config['files']
                                                     ['audible_download_dir']
                                              )
                    ,auth=auth
                    )
        else:
            logger.warning("Unhandled content_delivery_type: %s for %s  %s"
                          ,book['content_delivery_type']
                          ,book['asin']
                          ,book['title']
                          )


if __name__ == "__main__":
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(level=log_level)
    main()
