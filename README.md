# Splunk Alert Scheduler (SPLaSCH)

An extremely basic autonomous Splunk scheduler to trigger pusbullets alerts with your Splunk events

## Prerequisites:
- Splunk free instance
- python 3
- splunk python sdk (pip install splunk-sdk)
- pushbullet python module (pip install pushbullet.py)

## Features :
- runs scheduled searches on your Free Splunk instance, either locally or from a remote host
- fetches savedsearches directly from splunk and runs them based on a specific keyword (hourly, daily... there can be as many cron schedules as you need)
- pushes alerts via pushbullet (you could probably send emails too if you need)
- logs progress, which can be ingested in Splunk

## Drawbacks :
- no management of parallel runs: if your search takes forever, you will have many of them running at the same time
- very simplistic management of suppression, with a single key (though you can |eval your way around this) and only expressed in minutes
- modifying a rule does not reset suppression (only renaming it does)

## Setup:
- clone repository
- setup config.py
- create crontab entries (eg: `12 *   * * *        /usr/bin/python3 /home/scripts/splasch/splasch.py hourly12`)

## Splunk configuration example:

/opt/splunk/etc/apps/myApp/default/inputs.conf

	[http]
	disabled=0
	port=8088
	enableSSL=1

	[http://local_infra]
	description = HEC input for my infra logs
	token = myAwesomeToken123
	indexes = myIndex
	s2s_indexes_validation = enabled_for_all
	index = myIndex
	sourcetype = custom
	disabled = 0	


/opt/splunk/etc/apps/myApp/default/indexes.conf

	[myIndex]
	homePath   = $SPLUNK_DB/$_index_name/db
	coldPath   = $SPLUNK_DB/$_index_name/colddb
	thawedPath = $SPLUNK_DB/$_index_name/thaweddb

/opt/splunk/etc/apps/myApp/default/savedsearches.conf:

        [My awesome scheduled search]
        splasch_schedule = hourly12
        splasch_suppress_field = host
        splasch_suppress_minutes = 1440
        splasch_output = pushbullet
        search = index=* earliest=-1h | dedup host\
        | table _time, host

/opt/splunk/etc/apps/myApp/default/props.conf

	[splasch]
	KV_MODE = json
	TIME_PREFIX = "time"\s*:\s*
	TIME_FORMAT = %s.%3N

/opt/splunk/etc/apps/myApp/default/server.conf

	[general]
	allowRemoteLogin = always

	[license]
	active_group = Free

/opt/splunk/etc/apps/myApp/default/limits.conf

	[search]
	ttl = 259200

## syslog-ng configuration example to push splasch logs to splunk
	
	destination d_splunk_hec_splasch{
	    http(
        	url("https://splunk:8088/services/collector/raw?sourcetype=splasch")
	        method("POST")
        	log-fifo-size(1000000)
	        workers(2)
	        batch-lines(100)
	        batch-bytes(1024kb)
	        batch-timeout(60)
	        timeout(10)
	        headers("Authorization: Splunk myAwesomeToken123",  "Connection: keep-alive")
	        persist-name("splasch_to_splunk_hec")
	        response-action(400 => drop, 404 => retry)
	        disk-buffer(
	            compaction(yes)
	            mem-buf-length(2000)
	            reliable(no)
	            disk-buf-size(536870912)
	        )
	        tls(peer-verify(no))
	        use-system-cert-store(yes)
	        frac-digits(3)
	        body("${MSG}")
	    );
	};
	
	source s_splasch { file("/var/log/splasch.log" flags(no-parse)); };

	log { source(s_splasch); destination(d_splunk_hec_splasch); };	
