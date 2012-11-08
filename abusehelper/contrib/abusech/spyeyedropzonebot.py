"""
abuse.ch Zeus dropzone RSS feed.

Maintainer: Lari Huttunen <mit-code@huttu.net>
"""
import re
import socket
import urlparse
from abusehelper.core import bot, events
from abusehelper.contrib.rssbot.rssbot import RSSBot


class ZeusDropzoneBot(RSSBot):
    feeds = bot.ListParam(default=["https://spyeyetracker.abuse.ch/monitor.php?rssfeed=dropurls"])
    # Please note that this feed does *not* report ips per se. They are derived
    # below from the URL if the URL is an ip URL. That is why the ip is never
    # dropped if it is present in the source even if the bool param
    # treat_as_dns_source is set below.
    treat_as_dns_source = bot.BoolParam()

    def is_ip(self, string):
        for addr_type in (socket.AF_INET, socket.AF_INET6):
            try:
                socket.inet_pton(addr_type, string)
            except (ValueError, socket.error):
                pass
            else:
                return True
        return False

    def create_event(self, **keys):
        event = events.Event()
        # handle link data
        link = keys.get("link", None)
        if link:
            event.add("description url", link)
        # handle title data
        br = re.compile('[()]')
        title = keys.get("title")
        parts = []
        parts = title.split()
        tstamp = parts[1]
        tstamp = br.sub('', tstamp)
        event.add("source time", tstamp)
        # handle description data
        description = keys.get("description", None)
        if description:
            for part in description.split(","):
                pair = part.split(":", 1)
                if len(pair) < 2:
                    continue
                key = pair[0].strip()
                value = pair[1].strip()
                if not key or not value:
                    continue
                if key == "Status":
                    event.add(key.lower(), value)
                elif key == "SpyEye DropURL":
                    event.add("url", value)
                    parsed = urlparse.urlparse(value)
                    host = parsed.netloc
                    if self.is_ip(host):
                        event.add("ip", host)
                    else:
                        event.add("host", host)
        event.add("feed", "abuse.ch")
        event.add("malware", "SpyEye")
        event.add("type", "dropzone")
        return event

if __name__ == "__main__":
    ZeusDropzoneBot.from_command_line().execute()