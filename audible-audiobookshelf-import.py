#! /usr/bin/env python3

import audible
import os.path
import pathlib
import sqlite3
import audible_cli
import ffmpeg
import json

config = {}
#config['db'] = os.path.expanduser('~/.audible/audiobookshelf.db')
config['db'] = pathlib.Path.home() / '.audible' / 'audiobookshelf.db'
config['quality'] = 'best'
#config['audible_download_dir'] = os.path.expanduser('~/data/Audiobooks2/cli')
config['audible_download_dir'] = ( pathlib.Path.home()
                                 / 'data'
                                 / 'Audiobooks2'
                                 / 'Audible'
                                 / 'cli'
                                 )
#config['audible_auth_file'] = os.path.expanduser('~/.audible/audible.json')
config['audible_auth_file'] = pathlib.Path.home() / '.audible' / 'audible.json'
config['audiobookshelf_dir'] = pathlib.Path.home() / 'data' / 'Audiobooks'
config['activation_bytes'] = '2b6d2001'
config['tmp_dir'] = '/tmp'

def getAudibleLibrary():
    auth = audible.Authenticator.from_file(config['audible_auth_file'])
    with audible.Client(auth=auth) as client:
        library = []
        page = 1
        while True:
            books = client.get("library"
                              ,num_results=1000
                              ,page=page
                              ,response_groups=("contributors, media, price, "
                                                "product_attrs, product_desc, "
                                                "product_extended_attrs, "
                                                "product_plan_details, "
                                                "product_plans, rating, "
                                                "sample, sku, series, reviews, "
                                                "ws4v, origin, relationships, "
                                                "review_attrs, categories, "
                                                "badge_types, "
                                                "category_ladders, "
                                                "claim_code_url, "
                                                "is_downloaded, "
                                                "is_finished, is_returnable, "
                                                "origin_asin, pdf_url, "
                                                "percent_complete, "
                                                "provided_review"
                                               )
                              )
            library.extend(books['items'])
            if (len(books['items']) == 0):
                break
            page += 1
        return library

def setupDatabase(filename):
    con = sqlite3.connect(filename)
    cur = con.cursor()
    cur.execute('CREATE TABLE books(asin, title, location)')

if __name__ == "__main__":
    print("Getting library...")
    library = getAudibleLibrary()
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
        res = cur.execute("SELECT asin FROM books WHERE asin = ?"
                         ,(book['asin'],)
                         )
        if len(res.fetchall()) > 0:
            # This book has already been downloaded and added to the library so
            # move on to the next book in the list
            continue

        # Skip periodicals for now -- TODO
        if book['content_delivery_type'] == 'Periodical':
            continue

        # Download book 
        print('Trying to download:', book['asin'])
        audible_cli.cli.cli(['download'
                            ,'--asin', book['asin']
                            ,'--quality', config['quality']
                            ,'--output-dir', config['audible_download_dir']
                            ,'--filename-mode', 'asin_ascii'
                            ,'--aax-fallback'
                            ]
                           ,standalone_mode=False
                           )

        # Get downloaded filename
        # Check for aax file
        aaxPath = list(pathlib.Path(config['audible_download_dir'])
                          .glob(book['asin']+"*.aax")
                      )
        if len(aaxPath) > 0:
            # Convert to m4b
            tmpFile = config['tmp_dir'] / aaxPath[0].with_suffix(".m4b")
            aaxPath = config['audible_download_dir'] / aaxPath[0]
            (ffmpeg.input(aaxPath.as_posix()
                         ,activation_bytes=config['activation_bytes']
                         )
                   .output(tmpFile.as_posix(), codec='copy')
                   .run()
            )
        else:
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
        narrators = ', '.join([n['name'] for n in book['narrators']])

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
        tmpFile.rename(bookFilename)

        # Record it as having been added to the library
        res = cur.execute('INSERT INTO books (asin, title, location) values (?, ?, ?)'
                         ,(book['asin']
                          ,title
                          ,bookFilename.relative_to(config['audiobookshelf_dir']).as_posix()
                          )
                         )
        con.commit()
