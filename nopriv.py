#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2013 Remy van Elst
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.

#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.

#     You should have received a copy of the GNU General Public License
#     along with this program.  If not, see <http://www.gnu.org/licenses/>.

import imaplib
import email
import mailbox
from email.header import decode_header
from email.utils import parsedate
import time
import re
from math import ceil
import os
import base64
import cgi
import sys
import shutil
import errno
import datetime
import fileinput
import ConfigParser
from quopri import decodestring
import getpass

from jinja2 import Template
import md5
import sys
from HTMLParser import HTMLParser

# places where the config could be located
config_file_paths = [ 
    './nopriv.ini',
    './.nopriv.ini',
    '~/.config/nopriv.ini',
    '/opt/local/etc/nopriv.ini',
    '/etc/nopriv.ini'
]

config = ConfigParser.RawConfigParser()
found = False
for conf_file in config_file_paths:
    if os.path.isfile(conf_file):
        config.read(conf_file)
        found = True
        break
if found == False:
    message = "No config file found. Expected places: %s" % \
        ("\n".join(config_file_paths), )
    raise Exception(message)


IMAPSERVER = config.get('nopriv', 'imap_server')
IMAPLOGIN = config.get('nopriv', 'imap_user')
IMAPPASSWORD = config.get('nopriv', 'imap_password')

if IMAPPASSWORD == "":
    IMAPPASSWORD = getpass.getpass()

IMAPFOLDER_ORIG = [ folder.strip() for folder in \
                     config.get('nopriv', 'imap_folder').split(',') \
                     if folder.strip() != "" ]

yes_flags = ['true', 1, '1', 'True', 'yes', 'y', 'on']

ssl = False
try: 
    ssl_value = config.get('nopriv', 'ssl')
    if ssl_value in yes_flags: 
        ssl = True
except:
    pass

incremental_backup = False
try:
    incremental_value = config.get('nopriv', 'incremental_backup')
    if incremental_value in yes_flags: 
        incremental_backup = True
except:
    pass

offline = False
try:
    offline_value = config.get('nopriv', 'offline')
    if offline_value in yes_flags: 
        offline = True
except:
    pass

enable_html = True
CreateMailDir = True
messages_per_overview_page = 50
mailFolders = None

inc_location = "inc"

def connectToImapMailbox(IMAPSERVER, IMAPLOGIN, IMAPPASSWORD):
    if ssl is True:
        mail = imaplib.IMAP4_SSL(IMAPSERVER)
    if ssl is False:
        mail = imaplib.IMAP4(IMAPSERVER)
    mail.login(IMAPLOGIN, IMAPPASSWORD)
    return mail

maildir = 'mailbox.%s@%s' % (IMAPLOGIN, IMAPSERVER)
if not os.path.exists(maildir):
    os.mkdir(maildir)

maildir_raw = "%s/raw" % maildir
if not os.path.exists(maildir_raw):
    os.mkdir(maildir_raw)

maildir_result = "%s/html" % maildir
if not os.path.exists(maildir_result):
    os.mkdir(maildir_result)


def getTitle(title = None):
    """
    Returns title for all pges
    """

    result = []
    if title:
        result.append(title)
    
    result.append('%s@%s' % (IMAPLOGIN, IMAPSERVER))
    result.append('IMAP to local HTML')

    return ' | '.join(result)


def renderTemplate(templateFrom, saveTo, **kwargs):
    """
    Helper function to render a tamplete with variables
    """
    templateContents = ''
    with open("templates/%s" % templateFrom, "r") as f:
        templateContents = f.read()

    template = Template(templateContents)
    result = template.render(**kwargs)
    if saveTo:
        with open(saveTo, "w") as f:
            f.write(result.encode('utf-8'))

    return result


def renderMenu(selectedFolder = '', currentParent = '', linkPrefix = '.'):
    """
    Renders the menu on the left

    Expects: selected folder (id), currentParent (for recursion)
    """

    menuToShow = []
    folders = getMailFolders()
    for folderID in folders:
        folder = folders[folderID]
        if folder["parent"] != currentParent:
            continue

        menuToAdd = folder
        menuToAdd["children"] = renderMenu(selectedFolder, currentParent=folderID, linkPrefix=linkPrefix)
        menuToShow.append(menuToAdd)

    if len(menuToShow) <= 0:
        return ""

    menuToShow.sort(key=lambda val: val["title"])

    return renderTemplate("nav-ul.tpl", None, menuToShow=menuToShow, linkPrefix=linkPrefix)


def renderPage(saveTo, **kwargs):
    """
    HTML page wrapper

    Expects: title, contentZ
    """
    kwargs['title'] = getTitle(kwargs.get('title'))
    kwargs['username'] = IMAPLOGIN
    kwargs['linkPrefix'] = kwargs.get('linkPrefix', '.')
    kwargs['sideMenu'] = renderMenu(linkPrefix=kwargs['linkPrefix'])

    if (kwargs.get("headerTitle")):
        kwargs['header'] = renderHeader(kwargs.get("headerTitle"))

    return renderTemplate("html.tpl", saveTo, **kwargs)


def renderHeader(title):
    """
    Renders a simple header

    Expects: title
    """

    return renderTemplate("header-main.tpl", None, title=title)


def getMailFolders():
    """
    Returns mail folders
    """
    global mailFolders

    if not mailFolders is None:
        return mailFolders

    mailFolders = {}
    maillist = mail.list()
    for ifo in sorted(maillist[1]):
        ifo = re.sub(r"(?i)\(.*\)", "", ifo, flags=re.DOTALL)
        # TODO, maybe consider identifying separator 
        ifo = re.sub(r"(?i)\".\"", "", ifo, flags=re.DOTALL)
        ifo = re.sub(r"(?i)\"", "", ifo, flags=re.DOTALL)
        ifo = ifo.strip()

        parts = ifo.split(".")

        fileName = "%s/index.html" % ifo
        mailFolders[ifo] = {
            "id": ifo,
            "title": imaputf7decode(parts[len(parts) - 1]),
            "parent": '.'.join(parts[:-1]),
            "selected": ifo in IMAPFOLDER or imaputf7decode(ifo) in IMAPFOLDER,
            "folder": ifo,
            "file": fileName,
            "link": "/%s" % fileName,
        }

        if mailFolders[ifo]["selected"] and not os.path.exists("%s/%s" % (maildir_result, mailFolders[ifo]["folder"])):
            os.mkdir("%s/%s" % (maildir_result, mailFolders[ifo]["folder"]))

    # Single root folders do not matter really - usually it's just "INBOX"
    # Let's see how many menus existi with no parent
    menusWithNoParent = []
    for menu in mailFolders:
        if mailFolders[menu]["parent"] == "":
            menusWithNoParent.append(menu)

    # None found or more than one, go home
    if len(menusWithNoParent) == 1:
        # We remove it
        del mailFolders[menusWithNoParent[0]]

        # We change fatherhood for all children
        for menu in mailFolders:
            if mailFolders[menu]["parent"] == menusWithNoParent[0]:
                mailFolders[menu]["parent"] = ""

    return mailFolders


def returnHeader(title, inclocation=inc_location):
    response = """
<!DOCTYPE html>
<html lang="en">
<head>
        <title>%s</title>
        <link rel="stylesheet" type="text/css" href="%s/css/bootstrap.css" media="all" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
    <div class="row">
        <div class="col-md-12">
    """ % (title, inclocation)
    return response

def returnFooter():
    response = """
                    </div>
                <div class="col-md-8 col-md-offset-1 footer">
                <hr />
                Email backup made by <a href="https://raymii.org/s/software/Nopriv.py.html">NoPriv.py from Raymii.org</a>
                </div>
            </body>
        </html>
    """
    return response

lastfolder = ""
class DecodeError(Exception):
    pass

def decode_string(string):
    for charset in ("utf-8", 'latin-1', 'iso-8859-1', 'us-ascii', 'windows-1252','us-ascii'):
        try:
            return cgi.escape(unicode(string, charset)).encode('ascii', 'xmlcharrefreplace')
        except Exception:
            continue
    raise DecodeError("Could not decode string")

attCount = 0
lastAttName = ""
att_count = 0
last_att_filename = ""

def saveToMaildir(msg, mailFolder):
    global lastfolder
    global maildir_raw

    mbox = mailbox.Maildir(maildir_raw, factory=mailbox.MaildirMessage, create=True) 
    folder = mbox.add_folder(mailFolder)    
    folder.lock()
    try:
        message_key = folder.add(msg)
        folder.flush()

        maildir_message = folder.get_message(message_key)
        try:
            message_date_epoch = time.mktime(parsedate(decode_header(maildir_message.get("Date"))[0][0]))
        except TypeError as typeerror:
            message_date_epoch = time.mktime([2000, 1, 1, 1, 1, 1, 1, 1, 0])
        maildir_message.set_date(message_date_epoch)
        maildir_message.add_flag("s")


    finally:
        folder.unlock()
        folder.close()
        mbox.close()


def getLogFile():
    return "%s/%s" % (maildir_raw, 'proccess.txt')


def saveMostRecentMailID(mail_id, email_address, folder):
    return
    match = False
    for line in fileinput.input(getLogFile(), inplace = 1): 
        if line.split(":")[0] == folder and line.split(":")[1] == email_address and len(line) > 3:
            line = folder + ":" + email_address + ":" + str(mail_id)
            match = True
        if len(line) > 3 and line != "\n":
            print(line)
    fileinput.close()
    if match == False:
        with open(os.path.join(getLogFile()), 'a') as progress_file:
            progress_file.write("%s:%s:%d" % (folder, email_address, mail_id))
            progress_file.close()    



def getLastMailID(folder, email_address):
    if not os.path.exists(getLogFile()):
        with open(os.path.join(getLogFile()), 'w') as progress_file:
            progress_file.write("%s:%s:1" % (folder, email_address))
            progress_file.close()

    match = False
    with open(os.path.join(getLogFile()), 'r') as progress_file:
        for line in progress_file:
            if len(line) > 3:
                latest_mailid = line.split(":")[2]
                email_addres_from_file = line.split(":")[1]
                folder_name = line.split(":")[0]
                if folder_name == folder and email_addres_from_file == email_address:
                    progress_file.close()
                    return latest_mailid
        return 0
        progress_file.close()


def getHeader(raw, header):
    header = header.lower() + ':'

    lines = raw.split("\n")
    for line in lines:
        if not line.lower().startswith(header):
            continue


        response = line.strip()[len(header):]
        if header in ('from:', 'to:'):
            response = email.utils.parseaddr(response)[1]

        return response

def b64padanddecode(b):
    """Decode unpadded base64 data"""
    b+=(-len(b)%4)*'=' #base64 padding (if adds '===', no valid padding anyway)
    return base64.b64decode(b,altchars='+,').decode('utf-16-be')

def imaputf7decode(s):
    """Decode a string encoded according to RFC2060 aka IMAP UTF7.
    Minimal validation of input, only works with trusted data"""

    lst=s.split('&')
    out=lst[0]
    for e in lst[1:]:
        u,a=e.split('-',1) #u: utf16 between & and 1st -, a: ASCII chars folowing it
        if u=='' : out+='&'
        else: out+=b64padanddecode(u)
        out+=a
    return out


def get_messages_to_local_maildir(mailFolder, mail, startid = 1):
    global IMAPLOGIN
    mail.select(mailFolder, readonly=True)
    try:
        typ, mdata = mail.search(None, "ALL")
    except Exception as imaperror:
        print("Error in IMAP Query: %s." % imaperror)
        print("Does the imap folder \"%s\" exists?" % mailFolder)
        return

    total_messages_in_mailbox = len(mdata[0].split())
    last_mail_id = 0
    try:
        last_mail_id = mdata[0].split()[-1]
    except Exception:
        pass
    folder_most_recent_id = getLastMailID(mailFolder, IMAPLOGIN)

    if folder_most_recent_id > 2 and incremental_backup == True:
        if not int(folder_most_recent_id) == 1:
            startid = int(folder_most_recent_id) + 1
    if startid == 0:
        startid = 1

    for message_id in range(int(startid), int(total_messages_in_mailbox + 1)):
        result, data = mail.fetch(message_id , "(RFC822)")
        raw_email = data[0][1]
        print('Saving message %5s@%s: %s ~> %s' % (message_id, imaputf7decode(mailFolder), getHeader(raw_email, 'from'), getHeader(raw_email, 'to')))
        maildir_folder = mailFolder.replace("/", ".")
        saveToMaildir(raw_email, maildir_folder)
        if incremental_backup == True:
            saveMostRecentMailID(message_id, IMAPLOGIN, mailFolder)
        


def renderIndexPage():
    global IMAPFOLDER
    global IMAPLOGIN
    global IMAPSERVER
    global ssl
    global offline
    now = datetime.datetime.now()

    allInfo = []
    allInfo.append({
        "title": "IMAP Server",
        "value": IMAPSERVER,
    })

    allInfo.append({
        "title": "Username",
        "value": IMAPLOGIN,
    })

    allInfo.append({
        "title": "Date of backup",
        "value": str(now),
    })

    renderPage(
        "%s/%s" % (maildir_result, "index.html"),
        headerTitle="Email Backup index page",
        linkPrefix=".",
        content=renderTemplate(
            "page-index.html.tpl",
            None,
            allInfo=allInfo,
            linkPrefix=".",
        )
    )


def allFolders(IMAPFOLDER_ORIG, mail):
    response = []
    if len(IMAPFOLDER_ORIG) == 1 and IMAPFOLDER_ORIG[0] == "NoPriv_All":
        maillist = mail.list()
        for imapFolder in sorted(maillist[1]):
            imapFolder = re.sub(r"(?i)\(.*\)", "", imapFolder, flags=re.DOTALL)
            imapFolder = re.sub(r"(?i)\".\"", "", imapFolder, flags=re.DOTALL)
            imapFolder = re.sub(r"(?i)\"", "", imapFolder, flags=re.DOTALL)
            imapFolder = imapFolder.strip()
            response.append(imapFolder)
    else:
        response = IMAPFOLDER_ORIG
    return response

def returnImapFolders(available=True, selected=True, html=False):
    response = ""
    if available:
        if not html:
            response += "Available IMAP4 folders:\n"
        maillist = mail.list()
        for ifo in sorted(maillist[1]):
            ifo = re.sub(r"(?i)\(.*\)", "", ifo, flags=re.DOTALL)
            ifo = re.sub(r"(?i)\".\"", "", ifo, flags=re.DOTALL)
            ifo = re.sub(r"(?i)\"", "", ifo, flags=re.DOTALL)
            if html:
                response += "- %s <br />\n" % ifo
            else:
                response += "- %s \n" % ifo
        response += "\n"

    if selected:
        if html:
            response += "Selected folders: <br />\n"
        else:
            response += "Selected folders:\n"
        for sfo in IMAPFOLDER:
            if html:
                response += "- %s <br />\n" % sfo
            else:
                response += "- %s \n" % sfo
    if html:    
        response += "<br />\n"
    else:
        response += "\n"

    return response


def returnMenu(folderImIn, inDate = False, index = False, vertical = False, activeItem = ""):
    global IMAPFOLDER

    folder_number = folderImIn.split('/')
    current_folder = folder_number
    folder_number = len(folder_number)
    dotdotslash = "./"

    if vertical:
        response = '<ul class="nav nav-pills nav-stacked">'
    else:
        response = '<ul class="nav nav-pills">'

    if not index:
        for _ in range(int(folder_number)):
            dotdotslash += ""
        if inDate:
            dotdotslash += "../"
    if index:
        response += "\t<li class=\"active\"><a href=\"" + dotdotslash + "/index.html\">Index</a></li>\n"
    else:
        response += "\t<li><a href=\"" + dotdotslash + "/index.html\">Index</a></li>\n"


    for folder in IMAPFOLDER:
        if folder == activeItem:
            response += "\t<li class=\"active\"><a href=\"" + dotdotslash + folder + "/email-report-1.html\">" + imaputf7decode(folder).encode('utf-8') + "</a></li>\n"
        else:
            response += "\t<li><a href=\"" + dotdotslash + folder + "/email-report-1.html\">" + imaputf7decode(folder).encode('utf-8') + "</a></li>\n"

    if not index:
        response += "\t<li><a href=\"javascript:history.go(-1)\">Back</a></li>\n"
    else:
        response += "\t<li><a href=\"https://raymii.org\">Raymii.org</a></li>\n"
    response += "\n</ul>\n<hr />\n"

    return response

def remove(src):
    if os.path.exists(src):
        shutil.rmtree(src)

def copy(src, dst):
    try:
        shutil.copytree(src, dst)
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            shutil.copy(src, dst)
        elif exc.errno == errno.EEXIST:
            print("File %s already exists." % src)
        else: raise

def move(src, dst):
        shutil.move(src, dst)

def moveMailDir(maildir):
    print("Adding timestamp to Maildir.")
    now = datetime.datetime.now()
    maildirfilename = "Maildir." + str(now).replace("/", ".").replace(" ", ".").replace("-", ".").replace(":", ".")
    move(maildir, maildirfilename)

def returnWelcome():
    print("########################################################")
    print("# IMAP to Local HTML Backup by Charalampos Tsmipouris  #")
    print("########################################################")
    print("")
    print("Runtime Information:")
    print(sys.version)
    print("")


def extract_date(email):
    date = email.get('Date')
    return parsedate(date)


def getMailContent(mail):
    """
    Walks mail and returns mail content
    """
    content_of_mail_text = ""
    content_of_mail_html = ""
    attachments = []

    for part in mail.walk():
        part_content_maintype = part.get_content_maintype()
        part_content_type = part.get_content_type()
        part_charset = part.get_charsets()

        if part_content_type == 'text/plain':
            part_decoded_contents = part.get_payload(decode=True)
            try:
                if part_charset[0]:
                    content_of_mail_text += cgi.escape(unicode(str(part_decoded_contents), part_charset[0])).encode('ascii', 'xmlcharrefreplace')
                else:
                    content_of_mail_text += cgi.escape(str(part_decoded_contents)).encode('ascii', 'xmlcharrefreplace')
            except Exception:
                try:
                    content_of_mail_text +=  decode_string(part_decoded_contents)
                except DecodeError:
                    content_of_mail_text += "Error decoding mail contents."
                    print("Error decoding mail contents")
            continue

        if part_content_type == 'text/html':
            part_decoded_contents = part.get_payload(decode=True)
            try:
                if part_charset[0]:
                    content_of_mail_html += unicode(str(part_decoded_contents), part_charset[0]).encode('ascii', 'xmlcharrefreplace')
                else:
                    content_of_mail_html += str(part_decoded_contents).encode('ascii', 'xmlcharrefreplace')
            except Exception:
                try:
                    content_of_mail_html += decode_string(part_decoded_contents)
                except DecodeError:
                    content_of_mail_html += "Error decoding mail contents."
                    print("Error decoding mail contents")

            continue
    
        # Attachment
        if not part.get('Content-Disposition') is None:
            if part.get_content_maintype() == 'multipart':
                continue

            decoded_filename = part.get_filename()
            filename_header = None
            try:
                filename_header = decode_header(part.get_filename())
            except (UnicodeEncodeError, UnicodeDecodeError):
                filename_header = None

            if filename_header:
                filename_header = filename_header[0][0]
                attachment_filename = re.sub(r'[^.a-zA-Z0-9 :;,\.\?]', "_", filename_header.replace(":", "").replace("/", "").replace("\\", ""))
            else:
                attachment_filename = re.sub(r'[^.a-zA-Z0-9 :;,\.\?]', "_", decoded_filename.replace(":", "").replace("/", "").replace("\\", ""))
            
            attachment_content = part.get_payload(decode=True)
            attachments.append({
                "filename": attachment_filename,
                "mimetype": part_content_type,
                "maintype": part_content_maintype,
                "content": attachment_content,
                "size": len(attachment_content),
            })

    if content_of_mail_text:
        content_of_mail_text = re.sub(r"(?i)<html>.*?<head>.*?</head>.*?<body>", "", content_of_mail_text, flags=re.DOTALL)
        content_of_mail_text = re.sub(r"(?i)</body>.*?</html>", "", content_of_mail_text, flags=re.DOTALL)
        content_of_mail_text = re.sub(r"(?i)<!DOCTYPE.*?>", "", content_of_mail_text, flags=re.DOTALL)
        content_of_mail_text = re.sub(r"(?i)POSITION: absolute;", "", content_of_mail_text, flags=re.DOTALL)
        content_of_mail_text = re.sub(r"(?i)TOP: .*?;", "", content_of_mail_text, flags=re.DOTALL)
        content_of_mail_text = decodestring(content_of_mail_text)
        

    if content_of_mail_html:
        content_of_mail_html = re.sub(r"(?i)<html>.*?<head>.*?</head>.*?<body>", "", content_of_mail_html, flags=re.DOTALL)
        content_of_mail_html = re.sub(r"(?i)</body>.*?</html>", "", content_of_mail_html, flags=re.DOTALL)
        content_of_mail_html = re.sub(r"(?i)<!DOCTYPE.*?>", "", content_of_mail_html, flags=re.DOTALL)
        content_of_mail_html = re.sub(r"(?i)POSITION: absolute;", "", content_of_mail_html, flags=re.DOTALL)
        content_of_mail_html = re.sub(r"(?i)TOP: .*?;", "", content_of_mail_html, flags=re.DOTALL)
        content_of_mail_html = decodestring(content_of_mail_html)
    
    return content_of_mail_text, content_of_mail_html, attachments

    # h = HTMLParser()
    # return h.unescape(content_of_mail_text), h.unescape(content_of_mail_html), attachments


def backup_mails_to_html_from_local_maildir(folder):
    print "Processing folder: %s" % imaputf7decode(folderID),

    global maildir_raw

    mailList = {}

    local_maildir_folder = folder.replace("/", ".")
    local_maildir = mailbox.Maildir(os.path.join(maildir_raw), factory=None, create=True)
    try:
        maildir_folder = local_maildir.get_folder(local_maildir_folder)
    except mailbox.NoSuchMailboxError as e:
        renderPage(
            "%s/%s" % (maildir_result, mailFolders[folder]["file"]),
            headerTitle="Folder %s" % mailFolders[folder]["title"],
            content=renderTemplate(
                "page-mail-list.tpl",
                None,
                mailList=mailList,
            )
        )

        print("..Done!")
        return

    print "(%d)" % len(maildir_folder),
    for mail in maildir_folder:
        mail_subject = decode_header(mail.get('Subject'))[0][0]
        mail_subject_encoding = decode_header(mail.get('Subject'))[0][1]
        if not mail_subject_encoding:
            mail_subject_encoding = "utf-8"

        if not mail_subject:
            mail_subject = "(No Subject)"

        mail_from = email.utils.parseaddr(mail.get('From'))[1]

        mail_from_encoding = decode_header(mail.get('From'))[0][1]
        if not mail_from_encoding:
            mail_from_encoding = "utf-8"

        mail_to = email.utils.parseaddr(mail.get('To'))[1]
        mail_to_encoding = decode_header(mail.get('To'))[0][1]
        if not mail_to_encoding:
            mail_to_encoding = "utf-8"

        mail_date = email.utils.parsedate(decode_header(mail.get('Date'))[0][0])

        try:
            mail_subject = cgi.escape(unicode(mail_subject, mail_subject_encoding)).encode('ascii', 'xmlcharrefreplace')
            mail_to = cgi.escape(unicode(mail_to, mail_to_encoding)).encode('ascii', 'xmlcharrefreplace')
            mail_from = cgi.escape(unicode(mail_from, mail_from_encoding)).encode('ascii', 'xmlcharrefreplace')
        except Exception:
            mail_subject = decode_string(mail_subject)
            mail_to = decode_string(mail_to)
            mail_from = decode_string(mail_from)

        mail_id = mail.get('Message-Id')
        mail_id_hash = md5.new(mail_id).hexdigest()
        mail_folder = "%s/%s" % (mailFolders[folder]["folder"], str(time.strftime("%Y/%m/%d", mail_date)))

        try:
            os.makedirs("%s/%s" % (maildir_result, mail_folder))
        except:
            pass

        fileName = "%s/%s.html" % (mail_folder, mail_id_hash)
        content_of_mail_text, content_of_mail_html, attachments = "", "", []
        error_decoding = ""
        h = HTMLParser()

        try:
            content_of_mail_text, content_of_mail_html, attachments = getMailContent(mail)
        except Exception as e:
            error_decoding += "~> Error in getMailContent: %s" % str(e)

        try:
            content_of_mail_text = h.unescape(content_of_mail_text)
        except Exception as e:
            error_decoding += "~> Error decoding TEXT: %s" % str(e)

        try:
            content_of_mail_html = h.unescape(content_of_mail_html)
        except Exception as e:
            error_decoding += "~> Error decoding HTML: %s" % str(e)

        data_uri_to_download = "data:text/plain;base64,%s" % base64.b64encode(str(mail))

        content_default = "raw"
        if content_of_mail_text:
            content_default = "text"
        if content_of_mail_html:
            content_default = "html"

        mailList[mail_id] = {
            "id": mail_id,
            "from": mail_from,
            "to": mail_to,
            "subject": mail_subject,
            "date": str(time.strftime("%Y-%m-%d %H:%m", mail_date)),
            "size": len(str(mail)),
            "file": fileName,
            "link": "/%s" % fileName,
            "content": {
                "html": content_of_mail_html,
                "text": content_of_mail_text,
                "raw": decode_string(str(mail)),
                "default": content_default,
            },
            "download": {
                "filename": "%s.eml" % mail_id_hash,
                "content": data_uri_to_download,
            },
            "attachments": attachments,
            "error_decoding": error_decoding,
        }

        attachment_count = 0
        for attachment in attachments:
            attachment_count += 1
            attachment["path"] = "%s/%s-%02d-%s" % (mail_folder, mail_id_hash, attachment_count, attachment["filename"])
            with open("%s/%s" % (maildir_result, attachment["path"]), 'wb') as att_file:
                try:
                    att_file.write(attachment["content"])
                except Exception as e:
                    att_file.write("Error writing attachment: " + str(e) + ".\n")
                    print("Error writing attachment: " + str(e) + ".\n")

        renderPage(
            "%s/%s" % (maildir_result, mailList[mail_id]["file"]),
            headerTitle=mailList[mail_id]["subject"],
            linkPrefix="../../../..",
            content=renderTemplate(
                "page-mail.tpl",
                None,
                mail=mailList[mail_id],
                linkPrefix="../../../..",
            )
        )

        # No need to keep it in memory
        del mailList[mail_id]["content"]
        del mailList[mail_id]["download"]
        mailList[mail_id]["attachments"] = len(mailList[mail_id]["attachments"])

        print ".",
        sys.stdout.flush()
    print "Done!"

    print "    > Creating index file..",
    sys.stdout.flush()
    renderPage(
        "%s/%s" % (maildir_result, mailFolders[folder]["file"]),
        headerTitle="Folder %s (%d)" % (mailFolders[folder]["title"], len(mailList)),
        linkPrefix="..",
        content=renderTemplate(
            "page-mail-list.tpl",
            None,
            mailList=mailList,
            linkPrefix="..",
        )
    )

    print "Done!"

returnWelcome()

if not offline:
    mail = connectToImapMailbox(IMAPSERVER, IMAPLOGIN, IMAPPASSWORD)
    IMAPFOLDER = allFolders(IMAPFOLDER_ORIG, mail)
    print(returnImapFolders())
    
renderIndexPage()
remove("%s/inc" % maildir_result)
copy(inc_location, "%s/inc" % maildir_result)


if not offline:
    for folder in IMAPFOLDER:
        print(("Getting messages from server from folder: %s.") % imaputf7decode(folder))
        retries = 0
        if ssl:
            try:
                get_messages_to_local_maildir(folder, mail)
            except imaplib.IMAP4_SSL.abort:
                if retries < 5:
                    print(("SSL Connection Abort. Trying again (#%i).") % retries)
                    retries += 1
                    mail = connectToImapMailbox(IMAPSERVER, IMAPLOGIN, IMAPPASSWORD)
                    get_messages_to_local_maildir(folder, mail)
                else:
                    print("SSL Connection gave more than 5 errors. Not trying again")
        else:
            try:
                get_messages_to_local_maildir(folder, mail)
            except imaplib.IMAP4.abort:
                if retries < 5:
                    print(("Connection Abort. Trying again (#%i).") % retries)
                    retries += 1
                    mail = connectToImapMailbox(IMAPSERVER, IMAPLOGIN, IMAPPASSWORD)
                    get_messages_to_local_maildir(folder, mail)
                else:
                    print("Connection gave more than 5 errors. Not trying again")
                
        print(("Done with folder: %s.") % imaputf7decode(folder))
        print("\n")


for folderID in mailFolders:
    folder = mailFolders[folderID] 
    if not folder["selected"]:
        continue

    backup_mails_to_html_from_local_maildir(folderID)
    print("\n")

if not incremental_backup:
    moveMailDir(maildir_raw)
