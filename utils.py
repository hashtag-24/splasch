#!/usr/bin/python3
import sys
import time
import json
import sqlite3
from datetime import datetime
from urllib.parse import quote
import splunklib.results as results
from config import LOGFILE, ALERT_LINK, DEBUG

def log(msg, force=False, ident=""):
    """ log a message to LOGFILE """
    if force or DEBUG:
        if ident:
            msg = {"time": current_time(), "run_id": ident, "message": str(msg)}
        if isinstance(msg, dict):
            msg=json.dumps(msg)
        else:
            msg=str(msg)
        with open(LOGFILE, "a") as log:
            log.write(msg + "\n")


def init_db(dbcon):
    """ Create table to track alert suppression """
    """ in DBFILE if it does not exist          """
    cursor = dbcon.cursor()
    try:
        cursor.execute("select * from alert_suppress")
    except sqlite3.OperationalError:
        cursor.execute('''
        CREATE TABLE alert_suppress (
            ID  INTEGER PRIMARY KEY AUTOINCREMENT,
            NAME    TEXT        NOT NULL,
            UNTIL   INT         NOT NULL,
            VALUE     TEXT        NOT NULL
        )''')


def format_message(event, fields=None):
    """ Format pushbullet output message     """
    """ include only requested fields,       """
    """ or all fields if no requested fields """
    message = []
    try:
        fields_list = [x.strip() for x in fields.split(',')]
    except AttributeError:
        fields_list = list(event.keys())
    for k,v in event.items():
        if k in fields_list and (k =="_time" or k[0] != "_"):
            message.append("*" + k + ": " + str(v))
    return "\n".join(message)


def send_alert(pb, title, event, results_link=None, fields=None):
    """ Actually send the pushbullet alert """
    """ use event formatted to text        """
    """ include link to ALERT_LINK         """
    if results_link:
        uri_path = results_link
    else:
        uri_path =  "/app/search/search?q=" + quote("|savedsearch \"" + title + "\"")
    drilldown_link = ALERT_LINK + uri_path
    res = pb.push_link("SPLaSCH ALERT: " + title, drilldown_link, format_message(event, fields))
    return res.get("receiver_email")


def add_suppress_line(dbcon, name, value, until):
    """ insert suppress line into database """
    cursor = dbcon.cursor()
    cursor.execute("INSERT INTO alert_suppress (NAME, VALUE, UNTIL) values (\"" + name + "\", \"" + value + "\", " + str(until) + ")")
    dbcon.commit()


def should_suppress(dbcon, name, value, run_time):
    """ Query suppress database to know if  """
    """ trigger should be suppressed or not """
    if not value:
        return False
    cursor = dbcon.cursor()
    res = cursor.execute("SELECT ID from alert_suppress where NAME=\"" + name + "\" AND VALUE=\"" + value + "\" AND " + str(run_time) + " < UNTIL" )
    return not (res.fetchone() is None)


def db_cleanup(dbcon, run_time):
    """ remove expired suppresses """
    cursor = dbcon.cursor()
    cursor.execute("DELETE FROM alert_suppress WHERE UNTIL < " + str(run_time-1))
    dbcon.commit()


def get(obj, key):
    """ get 'key' from 'obj' if it exists """
    """ get an empty string otherwise     """
    try:
        return obj[key]
    except:
        return ""


def current_time():
    """ return current epoch time """
    return round(time.time(),3)


def search(query, service):
    """ use splunk SDK to run search          """
    """ and return useful infos               """
    """ as well as results in a list of dicts """
    job = service.jobs.create(query, exec_mode="normal")

    # A normal search returns the job's SID right away, so we need to poll for completion
    while True:
        while not job.is_ready():
            pass
        stats = {"isDone": job["isDone"],
            "doneProgress": float(job["doneProgress"])*100,
            "scanCount": int(job["scanCount"]),
            "eventCount": int(job["eventCount"]),
            "resultCount": int(job["resultCount"])}

        if stats["isDone"] == "1":
            break
    # Parse job id, could not find a property to simply retrieve it
    job_id = job.path.strip("/").split("/")[-1]
    stats["jobId"] = job_id
    job_link = "/app/search/search?sid=" + job_id

    search_results = []
    # Get the results in a list of dicts
    for result in results.JSONResultsReader(job.results(output_mode='json')):
        if isinstance(result, dict):
            search_results.append(result)

    # Return all the useful infos for stat nerds (:
    return {"stats":stats, "results_link": job_link, "results": search_results}


def run_failed(pb, json_output, msg, exception, run_id):
    json_output["run_status"] = "ERROR: " + msg
    if exception:
        json_output["run_status"] += ": " + str(exception)
    log("ERROR: " + msg + ". Exiting.", ident=run_id)
    log(json_output, force=True)
    if pb:
        send_alert(pb, "ERROR: " + msg, {"status":"error"}, "/app/search")
