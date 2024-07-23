#!/usr/bin/python3
import splunklib.client as client
import splunklib.binding as binding
import utils as u
import sqlite3
import sys
import json
import re
import uuid
import time
from pushbullet import Pushbullet
from config import HOST, PORT, USERNAME, PASSWORD, SPLUNKAPP, DBFILE, PUSHBULLET_APIKEY


def SplunkAlertScheduler():
    try:
        schedule_keyword = sys.argv[1]
    except IndexError:
        print("please provide schedule keyword to match in savedsearches")
        exit(1)

    run_id = str(uuid.uuid4())
    
    json_output = {"time": u.current_time(), "run_id": run_id, "schedule": schedule_keyword, "run_status":"success"}

    # Pushbullet connection OR fail & exit
    try:
        pb = Pushbullet(PUSHBULLET_APIKEY)
    except Exception as e:
        u.run_failed(None, json_output, "could not connect to pushbullet", str(type(e)).split("'")[1], run_id)
        exit(1)

    # DB connection OR fail & exit
    try:
        dbconn = sqlite3.connect(DBFILE)
        u.init_db(dbconn)
    except Exception as e:
        u.run_failed(pb, json_output, "could not open database file", str(e), run_id)
        exit(1)

    # gather connection parameters
    splunk_connect = {"host": HOST, "port": PORT, "username": USERNAME, "password": PASSWORD}
    try:
        if SPLUNKAPP:
            splunk_connect["app"] = SPLUNKAPP
    except NameError:
        #if SPLUNKAPP is nowhere to be found, use ALL apps
        pass

    # Splunk connection OR fail & exit
    try:
        service = client.connect(**splunk_connect)
    except ConnectionRefusedError as e:
        u.run_failed(pb, json_output, "could not connect to splunk", str(e), run_id)
        exit(1)

    try:
        splunk_health = service.info["health_info"]
    except KeyError as e:
        u.run_failed(pb, json_output, "splunk is not reachable", str(e), run_id)
        exit(1)

    if splunk_health not in ["green", "yellow"]:
        u.send_alert("MONIT: health info is " + str(splunk_health), {"status": "warning"}, "/app/search")

    json_output["splunk_health"] = splunk_health

    # Get all rules to run and parameters from splunk
    u.log("fetching savedsearches part of schedule named '" + schedule_keyword + "' from splunk", ident=run_id)
    executed_rules = 0
    json_rules_output = []
    for ss in service.saved_searches:
        # Memorize current time to know how long the search took
        run_time = u.current_time()
        # If the schedule is not the one requested, loop on savedsearches
        try:
            if ss.splasch_schedule == schedule_keyword and ss.disabled == "0":
                u.log("running rule='" + ss["name"] + "'", ident=run_id)
            else:
                continue
        except AttributeError:
            continue

        # For each rule, build a dict to log what happened
        rule_output = {"name": ss["name"], "run_time": 0, "status": "success", "messages":[]}
        
        # Use savedsearch command to actually run the rule
        search_string = "| savedsearch \"" + ss["name"] + "\""

        # If an output is requested, we need to specify that we want to retrieve all the fields 
        if u.get(ss, "splasch_output"):
            search_string += " | fields *"

        # Actually search in Splunk events
        try:
            res = u.search(search_string, service)
            executed_rules += 1
        except binding.HTTPError as e:
            rule_output["status"] = "failure"
            rule_output["messages"].append("ERROR: executing rule: " + str(e))
            rule_output["run_time"] = round(u.current_time()-run_time,3)
            json_rules_output.append(rule_output)
            u.log("WARN " + ss["name"] + ": " + str(e), ident=run_id)
            continue
        
        search_results = res["results"]
        
        rule_output["results"] = {"success": 0, "failure": 0, "warning": 0, "suppressed": 0, "total": 0}
        rule_output["results"]["total"] = len(search_results)
        rule_output["job"] = {"link": res["results_link"], "stats": res["stats"]}

        # If pushbullet output is requested, loop on each results
        if u.get(ss, "splasch_output") == "pushbullet":
            for item in search_results:
                if not isinstance(item, dict):
                    continue
                # If suppress is enabled for this rule, first check if we need to suppress this result
                if u.get(ss, "splasch_suppress_field"):
                    suppress_until = run_time + (60 * int(ss["splasch_suppress_minutes"]))
                    suppress_value = item[ss["splasch_suppress_field"]]
                    # If suppress is necessary, loop to the next result
                    if u.should_suppress(dbconn, ss["name"], suppress_value, int(run_time)):
                        rule_output["results"]["suppressed"] += 1
                        continue
                    # Else, add suppress condition to database before triggering alert
                    u.add_suppress_line(dbconn, ss["name"], suppress_value, suppress_until)

                # Actual sending of the alert, only output requested fields
                sent_to = u.send_alert(pb, ss["name"], item, rule_output["job"]["link"], u.get(ss, "splasch_output_fields"))
                # Increment relevant counters
                if sent_to :
                    rule_output["results"]["success"] += 1
                    u.log("'" + ss["name"] + "': alert sent to " + str(sent_to), ident=run_id)
                else:
                    rule_output["results"]["failure"] += 1
                    rule_output["messages"].append("ERROR while sending pushbullet alert")
                    u.log("'" + ss["name"] + "': ERROR while sending pushbullet alert", ident=run_id)
    
        # Compute run_time for the rule
        rule_output["run_time"] = round(u.current_time()-run_time,3)
        json_rules_output.append(rule_output)

        # Remove expired suppresses
        u.db_cleanup(dbconn, run_time)

    json_output["rules"] = json_rules_output

    if executed_rules>0:
        u.log(str(executed_rules) + " rule(s) were ran successfully.", ident=run_id)
    elif len(json_rules_output)>0:
        # searches were ran but none were successful
        u.run_failed(pb, json_output, "All rules failed to be executed", "", run_id)
    else:
        u.log("No rule has been run.", ident=run_id)
    
    # Log the whole run
    u.log(json_output, force=True)

if __name__ == '__main__':
    SplunkAlertScheduler()
