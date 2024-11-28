#! /usr/bin/env python3

import json
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

def get_audible_library():
    if not auth:
        auth = audible.Authenticator.from_file(config['audible_auth_file'])
    with audible.Client(auth=auth) as client:
        library = []
        page = 1
        while True:
            print(f"...Retrieving library index page {page}...")
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


def setupDatabase(filename):
    con = sqlite3.connect(filename)
    cur = con.cursor()
    cur.execute('CREATE TABLE books(asin, title, location)')


def download_book_as_aax(asin, quality, download_dir, filename_mode='asin_ascii'):
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


def download_podcast(podcast, auth=None):
    seasons = [child for child in podcast['relationships']
               if     child['relationship_to_product'] == 'child'
                  and child['relationship_type'] == 'season'
              ]
    if seasons:
        # Podcast is organized into seasons, so download by season
        #TODO
        for season in seasons:
            # Download season information
            season_info = get_audible_product(season['asin'])
            episodes_asins = [ child['asin'] for child in
                              season_info['relationships']
    else:
        # Podcase is not organized into seasons, so download by episodes
        #TODO
        pass

def main():
    print("Getting library...")
    library = get_audible_library()
    print("Checking for db...")
    if not os.path.exists(config['db']):
        print('Setting up database at', config['db'])
        setupDatabase(config['db'])
    print("Connecting to db...")
    con = sqlite3.connect(config['db'])
    cur = con.cursor()
    print("Handling library...")
    for book in library:
        # Check if book has already been downloaded and added to library
        print("ASIN:", book['asin'])
        if book['asin'] in asin_to_skip:
            continue
        res = cur.execute("SELECT asin FROM books WHERE asin = ?"
                         ,(book['asin'],)
                         )
        if len(res.fetchall()) > 0:
            # This book has already been downloaded and added to the library so
            # move on to the next book in the list
            continue

        # Skip periodicals for now -- TODO
        if book['content_delivery_type'] == 'Periodical':
            print("Skipping because it is of content delivery type Periodical")
            continue

        # Skip podcasts for now -- TODO
        if book['content_delivery_type'] == 'PodcastParent':
            print("Skipping because it is of content delivery type PodcastParent")
            continue

        # Download book as aax
        print('Trying to download as aax:', book['asin'])
        aax_path = download_book_as_aax(asin=book['asin']
                                       ,quality=config['quality']
                                       ,download_dir=config['audible_donwnload_dir']
                                       )
        if len(aax_path) > 0:
            # Convert to m4b
            tmpFile = config['tmp_dir'] / aax_path[0].with_suffix(".m4b")
            aax_path = config['audible_download_dir'] / aax_path[0]
            (ffmpeg.input(aax_path.as_posix()
                         ,activation_bytes=config['activation_bytes']
                         )
                   .output(tmpFile.as_posix(), codec='copy')
                   .run()
            )
        else:
            # Download book as aaxc
            print('Trying to download as aaxc:', book['asin'])
            audible_cli.cli.cli(['download'
                                ,'--asin', book['asin']
                                ,'--quality', config['quality']
                                ,'--output-dir', config['audible_download_dir']
                                ,'--filename-mode', 'asin_ascii'
                                ,'--aaxc'
                                ]
                               ,standalone_mode=False
                               )

            # Check for aaxc file
            aaxcPath = list(config['audible_download_dir'].glob(book['asin']+"*.aaxc"))
            voucherPath = list(config['audible_download_dir'].glob(book['asin']+"*.voucher"))
            if len(aaxcPath) > 0 and len(voucherPath) > 0:
                tmpFile = config['tmp_dir'] / aaxcPath[0].with_suffix(".m4b")
                aaxcPath = config['audible_download_dir'] / aaxcPath[0]

                # Extract license
                voucherPath = config['audible_download_dir'] / voucherPath[0]
                voucher = json.load(voucherPath.open('r'))
                voucherKey = voucher['content_license']['license_response']['key']
                voucherIv = voucher['content_license']['license_response']['iv']

                # Convert to m4b
                (ffmpeg.input(aaxcPath.as_posix()
                             ,activation_bytes=config['activation_bytes']
                             ,audible_key=voucherKey
                             ,audible_iv=voucherIv
                             )
                       .output(tmpFile.as_posix(), codec='copy')
                       .run()
                )
            else:
                print("No aax or aaxc file for this title:", book['asin'], book['title'])
                continue

        # Put it in place in the audiobookshelf
        author = ', '.join([a['name'] for a in book['authors']])
        if book['series']:
            series = book['series'][0]['title']
            seriesPosition = book['series'][0]['sequence']
        else:
            series = ""
            seriesPosition = ""
        title = book['title']
        subtitle = book['subtitle']
        if book['narrators']:
            narrators = ', '.join([n['name'] for n in book['narrators']])
        else:
            narrators = ''

        bookDirectory = config['audiobookshelf_dir'] / author
        if series:
            bookDirectory = bookDirectory / series
            if seriesPosition:
                title = seriesPosition + ' - ' + title
        if subtitle:
            title = title + ' - ' + subtitle
        if narrators:
            titleLen = len(title) + 3
            if titleLen + len(narrators) > 255:
                narrators = ""
                for n in book['narrators']:
                    if titleLen + len(narrators) + len(n['name']) < 254:
                        if narrators == "":
                            narrators = n['name']
                        else:
                            narrators = narrators + ', ' + n['name']
            title = title + ' {' + narrators + '}'


        bookDirectory = bookDirectory / title

        bookDirectory.mkdir(parents=True, exist_ok=True)
        bookFilename = bookDirectory / tmpFile.name
        shutil.move(str(tmpFile), str(bookFilename))

        # Record it as having been added to the library
        res = cur.execute('INSERT INTO books (asin, title, location) values (?, ?, ?)'
                         ,(book['asin']
                          ,title
                          ,bookFilename.relative_to(config['audiobookshelf_dir']).as_posix()
                          )
                         )
        con.commit()

if __name__ == "__main__":
    main()
