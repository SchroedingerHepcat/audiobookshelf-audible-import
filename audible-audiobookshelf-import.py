#! /usr/bin/env python3

import json
import logging
import os
import os.path
import pathlib
import shutil
import sqlite3

import audible
import audible_cli
import ffmpeg

# Haven't figured out how to do podcasts/episodes yet
asin_to_skip = ['B0CK8RZ2GR'
               ,'B0BR2QK8M7'
               ,'B0BJ7BV4PH'
               ,'B08JCLTS2P'
               ]

config = {}
#config['db'] = os.path.expanduser('~/.audible/audiobookshelf.db')
config['db'] = pathlib.Path.home() / '.audible' / 'audiobookshelf.db'
config['quality'] = 'best'
#config['audible_download_dir'] = os.path.expanduser('~/data/Audiobooks2/Audible/cli')
config['audible_download_dir'] = pathlib.Path.home() / 'data' / 'Audiobooks2' / 'Audible' / 'cli'
#config['audible_auth_file'] = os.path.expanduser('~/.audible/audible.json')
config['audible_auth_file'] = pathlib.Path.home() / '.audible' / 'audible.json'
config['audiobookshelf_dir'] = pathlib.Path.home() / 'data' / 'Audiobooks'
config['activation_bytes'] = '2b6d2001'
config['tmp_dir'] = '/tmp'




def get_audible_library(auth=None):
    logger = logging.getLogger(__name__)
    if not auth:
        auth = audible.Authenticator.from_file(config['audible_auth_file'])
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
        auth = audible.Authenticator.from_file(config['audible_auth_file'])
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
    aax_path = list(pathlib.Path(config['audible_download_dir'])
                   .glob(f"{asin}*.aax")
                   )
    return aax_path


def download_product_as_aax(asin
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
                        ,'--aax'
                        ]
                       ,standalone_mode=False
                       )

    # Get downloaded filename
    # Check for aax file
    aax_path = list(pathlib.Path(config['audible_download_dir'])
                   .glob(f"{asin}*.aax")
                   )
    return aax_path


def download_product_as_aaxc(asin
                            ,quality
                            ,download_dir
                            ,filename_mode='asin_ascii'
                            ):
    os.chdir(download_dir)
    audible_cli.cli.cli(['download'
                        ,'--asin', asin
                        ,'--quality', config['quality']
                        ,'--output-dir', config['audible_download_dir']
                        ,'--filename-mode', 'asin_ascii'
                        ,'--aaxc'
                        ]
                       ,standalone_mode=False
                       )

    # Check for aaxc file
    aaxc_paths = [   p.resolve()
                 for p
                 in  config['audible_download_dir'].glob(f'{asin}*.aaxc')
                 ]
    voucher_paths = [  p.resolve()
                    for p
                    in config['audible_download_dir'].glob(f'{asin}*.voucher')
                    ]
    return (aaxc_paths, voucher_paths)


def download_podcast_episode(asin
                            ,download_dir
                            ,quality='best'
                            ,filename_mode='asin_ascii'
                            ):
    #TODO
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
        episode_m4b_path = convert_aaxc_to_m4b(aaxc_paths, voucher_paths)

    return episode_m4b_path


def convert_aax_to_m4b(aax_paths, output_dir=None):
    if not output_dir:
        output_dir = config['tmp_dir']
    output_dir = pathlib.Path(output_dir)
    aax_path = aax_paths[0]
    m4b_file = (output_dir / aax_path.name).with_suffix(".m4b")
    (ffmpeg.input(aax_path.as_posix()
                 ,activation_bytes=config['activation_bytes']
                 )
           .output(m4b_file.as_posix(), codec='copy')
           .run()
    )
    return m4b_file


def convert_aaxc_to_m4b(aaxc_paths, voucher_paths, output_dir=None):
    if not output_dir:
        output_dir = config['tmp_dir']
    output_dir = pathlib.Path(output_dir)
    aaxc_path = aaxc_paths[0]
    m4b_file = (output_dir / aaxc_path.name).with_suffix(".m4b")

    # Extract license
    voucher_path = voucher_paths[0]
    voucher = json.load(voucher_path.open('r'))
    voucker_key = voucher['content_license']['license_response']['key']
    voucher_iv = voucher['content_license']['license_response']['iv']

    # Convert to m4b
    (ffmpeg.input(aaxc_path.as_posix()
                 ,activation_bytes=config['activation_bytes']
                 ,audible_key=voucker_key
                 ,audible_iv=voucher_iv
                 )
           .output(m4b_file.as_posix(), codec='copy')
           .run()
    )
    return m4b_file


def download_podcast(podcast, download_dir, import_db, auth=None):
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
    else:
        # Podcase is not organized into seasons, so download by episodes
        episode_asins = [child['asin'] for child
                         in season_info['relationships']
                         if     child['relationship_to_product'] == 'child'
                            and child['relationship_type'] == 'episode'
                        ]
    # Download episodes
    #TODO
    for asin in episode_asins:
        if import_db.is_already_imported(asin):
            continue
        episode_info = get_audible_product(asin=asin, auth=auth)
        episode_path = download_podcast_episode(asin, download_dir)


def import_audiobook_into_audiobookshelf(m4b_file, book_info, abs_dir):
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

    book_dir = config['audiobookshelf_dir'] / author
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
    abs_path = shutil.move(m4b_file, book_dir / m4b_file.name)
    return title, abs_path


class ImportDatabase:
    '''
    Tracks what files have already been imported into audiobookshelf to prevent
    constant redownloading, reconverting, and/or reimporting of files
    '''
    def __init__(self, db_file):
        self.con = sqlite3.connect(db_file)
        self.cur = self.con.cursor()
        self.setupDatabase(config['db'])

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
        self.cur.execute('INSERT INTO books (asin, title, location) '
                         'values (?, ?, ?)'
                        ,(asin, title, abs_path.relative_to(abs_dir).as_posix())
                        )
        self.con.commit()

    def setupDatabase(self, filename):
        con = sqlite3.connect(filename)
        cur = con.cursor()
        cur.execute('CREATE TABLE if not exists books(asin, title, location)')
        cur.execute('CREATE TABLE if not exists podcast_episodes(asin, title, '
                    'location)'
                   )


def main():
    logger = logging.getLogger(__name__)
    logger.info("Getting library...")
    auth = audible.Authenticator.from_file(config['audible_auth_file'])
    library = get_audible_library(auth)
    logger.info("Connecting to db...")
    db = ImportDatabase(config['db'])
    logger.info("Handling library...")
    for book in library:
        # Check if book has already been downloaded and added to library
        logger.debug("ASIN:", book['asin'])
        if book['asin'] in asin_to_skip:
            continue

        if db.is_book_already_imported(book['asin']):
            # This book has already been downloaded and added to the library so
            # move on to the next book in the list
            continue

        # Check if book is published yet
        #TODO

        # Skip periodicals for now -- TODO
        if book['content_delivery_type'] == 'Periodical':
            logging.warning("Skipping because it is of content delivery type "
                            "Periodical: %s  %s"
                           ,book['asin']
                           ,book['title']
                           )
            continue

        # Skip podcasts for now -- TODO
        if book['content_delivery_type'] == 'PodcastParent':
            logging.warning("Skipping because it is of content delivery type "
                            "PodcastParent: %s  %s"
                           ,book['asin']
                           ,book['title']
                           )
            continue

        # Download book as aax
        logger.info('Trying to download as aax:', book['asin'])
        aax_paths = download_product_as_aax(
                         asin=book['asin']
                        ,quality=config['quality']
                        ,download_dir=config['audible_download_dir']
                        )
        if len(aax_paths) > 0:
            tmp_m4b_file = convert_aax_to_m4b(aax_paths, output_dir=config['tmp_dir'])
        else:
            # Download book as aaxc
            logger.info('Trying to download as aaxc:', book['asin'])
            aaxc_paths, voucher_paths = download_product_as_aaxc(
                 book['asin']
                 ,quality=config['quality']
                 ,download_dir=config['audible_download_dir']
                 ,filename_mode='asin_ascii'
            )

            # Check for aaxc file
            if len(aaxc_paths) > 0 and len(voucher_paths) > 0:
                tmp_m4b_file = convert_aaxc_to_m4b(aaxc_paths=aaxc_paths
                                                  ,voucher_paths=voucher_paths
                                                  ,output_dir=config['tmp_dir']
                                                  )

            else:
                logger.warning("No aax or aaxc file for this title: ASIN: %s "
                               "Title: %s"
                              ,book['asin']
                              ,book['title']
                              )
                continue

        # Put it in place in the audiobookshelf
        title, abs_path = import_audiobook_into_audiobookshelf(
             m4b_file=tmp_m4b_file
            ,book_info=book
            ,abs_dir=config['audiobookshelf_dir']
        )

        # Record it as having been added to the library
        db.record_book_as_imported(asin=book['asin']
                                  ,title=title
                                  ,abs_path=abs_path
                                  ,abs_dir=config['audiobookshelf_dir']
                                  )

if __name__ == "__main__":
    main()
