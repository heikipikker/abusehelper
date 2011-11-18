import re
import os
import gzip
import inspect
from cStringIO import StringIO

import idiokit
from idiokit.xmlcore import Element, Elements

_ESCAPE = re.compile(u"&(?=#)|[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFF\uFFFE]",
                     re.U)

def _escape_sub(match):
    return "&#x%X;" % ord(match.group())

def _escape(string):
    """Return a string where forbidden XML characters (and & in some
    cases) have been escaped using XML character references.

    >>> _escape(u"\u0000\uffff")
    u'&#x0;&#xFFFF;'

    & should only be escaped when it is potentially a part of an escape
    sequence starting with &#.

    >>> _escape(u"& &#x26;")
    u'& &#x26;#x26;'

    Other characters are not affected.
    """
    return _ESCAPE.sub(_escape_sub, string)

_UNESCAPE = re.compile(u"&#x([0-9a-f]+);", re.I)

def _unescape_sub(match):
    value = match.group(1)
    try:
        return unichr(int(value, 16))
    except ValueError:
        return match.group(1)

def _unescape(string):
    """Return a string where XML character references have been
    substituted with the corresponding unicode characters.

    >>> _unescape(u"&#x0;&#xFFFF;")
    u'\\x00\\uffff'
    """

    return _UNESCAPE.sub(_unescape_sub, string)

def _normalize(value):
    """Return the value converted to unicode. Raise a TypeError if the
    value is not a string.

    >>> _normalize("a")
    u'a'
    >>> _normalize(u"b")
    u'b'
    >>> _normalize(1)
    Traceback (most recent call last):
    ...
    TypeError: expected a string value, got the value 1 of type int

    When converting str objects the default encoding is tried, and an
    UnicodeDecodeError is raised if the value can not bot converted.

    >>> _normalize("\xe4") #doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    ...
    UnicodeDecodeError: <the error goes here>
    """

    if isinstance(value, basestring):
        return unicode(value)

    name = type(value).__name__
    module = inspect.getmodule(value)
    if module is not None and module.__name__ != "__builtin__":
        name = module.__name__ + "." + name
    msg = "expected a string value, got the value %r of type %s" % (value, name)
    raise TypeError(msg)

EVENT_NS = "abusehelper#event"

_UNICODE_QUOTE_CHECK = re.compile(r'[\s"\\,=]', re.U)
_UNICODE_QUOTE = re.compile(r'["\\]', re.U)
def _unicode_quote(string):
    r"""
    >>> _unicode_quote(u"a")
    u'a'
    >>> _unicode_quote(u"=")
    u'"="'
    >>> _unicode_quote(u"\n")
    u'"\n"'
    """

    if _UNICODE_QUOTE_CHECK.search(string):
        return u'"' + _UNICODE_QUOTE.sub(r'\\\g<0>', string) + u'"'
    return string

_UNICODE_UNQUOTE = re.compile(r'\\(.)', re.U)
_UNICODE_PART = re.compile(r'\s*(?:(?:"((?:\\"|[^"])*)")|([^\s"=,]+)|)\s*',
                           re.U)
def _unicode_parse_part(string, start):
    match = _UNICODE_PART.match(string, start)
    quoted, unquoted = match.groups()
    end = match.end()

    if quoted is not None:
        return _UNICODE_UNQUOTE.sub("\\1", quoted), end
    if unquoted is not None:
        return unquoted, end
    return u"", end

class Event(object):
    __slots__ = ["_attrs"]

    _UNDEFINED = object()

    @classmethod
    def from_unicode(cls, string):
        r"""
        >>> event = Event()
        >>> event.add(u"a", u"b")
        >>> Event.from_unicode(unicode(event)) == event
        True

        >>> event.add(u'=', u'"')
        >>> Event.from_unicode(unicode(event)) == event
        True
        """

        result = cls()

        string = string.strip()
        if not string:
            return result

        index = 0
        length = len(string)

        while True:
            key, index = _unicode_parse_part(string, index)
            if index >= length:
                raise ValueError("unexpected string end")
            if string[index] != u"=":
                raise ValueError("unexpected character %r at index %d" %
                                 (string[index], index))
            index += 1

            value, index = _unicode_parse_part(string, index)
            result.add(key, value)

            if index >= length:
                return result

            if string[index] != u",":
                raise ValueError("unexpected character %r at index %d" %
                                 (string[index], index))
            index += 1

    @classmethod
    def from_elements(self, elements):
        """Yield events parsed from XML element(s).

        >>> element = Element("message")
        >>> list(Event.from_elements(element))
        []
        >>> element.add(Element("event", xmlns=EVENT_NS))
        >>> list(Event.from_elements(element)) == [Event()]
        True

        >>> event = Event()
        >>> event.add("key", "value")
        >>> event.add("\uffff", "\x05") # include some forbidden XML chars
        >>> element = Element("message")
        >>> element.add(event.to_element())
        >>> list(Event.from_elements(element)) == [event]
        True
        """

        for event_element in elements.children("event", EVENT_NS):
            event = Event()
            for attr in event_element.children("attr").with_attrs("key", "value"):
                key = _unescape(attr.get_attr("key"))
                value = _unescape(attr.get_attr("value"))
                event.add(key, value)
            yield event

    def __init__(self, *events):
        """
        Regression test: Keep the the correct internal encoding in the
        copy/merge constructor.

        >>> e1 = Event()
        >>> e1.add(u"\xe4", u"\xe4")
        >>> e2 = Event(e1)
        >>> e2.items()
        ((u'\\xe4', u'\\xe4'),)
        """

        self._attrs = dict()

        for event in events:
            for key, value in event.items():
                self.add(key, value)

    def add(self, key, value, *values):
        """Add value(s) for a key.

        >>> event = Event()
        >>> event.add("key", "1")
        >>> event.values("key")
        (u'1',)

        More than one value can be added with one call.

        >>> event = Event()
        >>> event.add("key", "1", "2")
        >>> sorted(event.values("key"))
        [u'1', u'2']

        Key-value pairs is already contained by the event are ignored.

        >>> event = Event()
        >>> event.add("key", "1")
        >>> event.values("key")
        (u'1',)
        >>> event.add("key", "1")
        >>> event.values("key")
        (u'1',)
        """

        self.update(key, (value,) + values)

    def update(self, key, values):
        """Update the values of a key.

        >>> event = Event()
        >>> event.update("key", ["1", "2"])
        >>> sorted(event.values("key"))
        [u'1', u'2']

        The event will not be modified if there are no values to add.

        >>> event = Event()
        >>> event.update("key", [])
        >>> event.contains("key")
        False
        """

        key = _normalize(key)
        if key not in self._attrs:
            self._attrs[key] = set()
        self._attrs[key].update(_normalize(value) for value in values)

    def discard(self, key, value, *values):
        """Discard some value(s) of a key.

        >>> event = Event()
        >>> event.add("key", "1", "2", "3")
        >>> event.discard("key", "1", "3")
        >>> event.values("key")
        (u'2',)

        Values that don't exist for the given key are silently ignored.

        >>> event = Event()
        >>> event.add("key", "2")
        >>> event.discard("key", "1", "2")
        >>> event.values("key")
        ()
        """

        key = _normalize(key)
        if key not in self._attrs:
            return
        valueset = self._attrs[key]
        valueset.difference_update(_normalize(value) for value in (value,) + values)
        if not valueset:
            del self._attrs[key]

    def clear(self, key):
        """Clear all values of a key.

        >>> event = Event()
        >>> event.add("key", "1")
        >>> event.clear("key")
        >>> event.contains("key")
        False

        Clearing keys that do not exist does nothing.

        >>> event = Event()
        >>> event.clear("key")
        """

        key = _normalize(key)
        self._attrs.pop(key, None)

    def _unkeyed(self):
        for values in self._attrs.itervalues():
            for value in values:
                yield value

    def _iter(self, key, parser, filter):
        if key is self._UNDEFINED:
            values = set(self._unkeyed())
        else:
            key = _normalize(key)
            values = self._attrs.get(key, ())

        if parser is not None:
            parsed = (parser(x) for x in values)

            if filter is not None:
                return (x for x in parsed if filter(x))
            else:
                return (x for x in parsed if x is not None)

        if filter is not None:
            return (x for x in values if filter(x))

        return values

    def values(self, key=_UNDEFINED, parser=None, filter=None):
        """Return a tuple of event values (for a specific key, if
        given).

        >>> event = Event()
        >>> event.add("key", "1", "2")
        >>> event.add("other", "3", "4")
        >>> sorted(event.values())
        [u'1', u'2', u'3', u'4']
        >>> sorted(event.values("key"))
        [u'1', u'2']

        Perform parsing, validation and filtering by passing in
        parsing and filtering functions (by default all None objects
        are filtered when a parsing function has been given).

        >>> import socket
        >>> def ipv4(string):
        ...     try:
        ...         return socket.inet_ntoa(socket.inet_aton(string))
        ...     except socket.error:
        ...         return None
        >>> event = Event()
        >>> event.add("key", "1.2.3.4", "abba")
        >>> event.add("other", "10.10.10.10")
        >>> event.values("key", parser=ipv4)
        ('1.2.3.4',)
        >>> sorted(event.values(parser=ipv4))
        ['1.2.3.4', '10.10.10.10']
        """

        return tuple(self._iter(key, parser, filter))

    def value(self, key=_UNDEFINED, default=_UNDEFINED,
              parser=None, filter=None):
        """Return one event value (for a specific key, if given).

        The value can be picked either from the values of some
        specific key or amongst event values.

        >>> event = Event()
        >>> event.add("key", "1")
        >>> event.add("other", "2")
        >>> event.value("key")
        u'1'
        >>> event.value() in [u"1", u"2"]
        True

        A default return value can be defined in case no suitable
        value is available:

        >>> event = Event()
        >>> event.value("key", "default value")
        'default value'
        >>> event.value(default="default value")
        'default value'

        KeyError is raised if no suitable values are available and no
        default is given.

        >>> event = Event()
        >>> event.value()
        Traceback (most recent call last):
        ...
        KeyError: 'no value available'
        >>> event.value("somekey")
        Traceback (most recent call last):
        ...
        KeyError: 'somekey'

        As with .values(...), parsing and filtering functions can be
        given, and they will be used to modify the results.

        >>> def int_parse(string):
        ...     try:
        ...         return int(string)
        ...     except ValueError:
        ...         return None
        >>> event = Event()
        >>> event.add("key", "1", "a")
        >>> event.value(parser=int_parse)
        1
        >>> event.value("key", parser=int_parse)
        1
        >>> event.value("other", parser=int_parse)
        Traceback (most recent call last):
        ...
        KeyError: 'other'
        """

        for value in self._iter(key, parser, filter):
            return value

        if default is self._UNDEFINED:
            if key is self._UNDEFINED:
                raise KeyError("no value available")
            raise KeyError(key)
        return default

    def contains(self, key=_UNDEFINED, value=_UNDEFINED,
                 parser=None, filter=None):
        """Return whether the event contains a key-value pair (for
        specific key and/or value, if given).

        >>> event = Event()
        >>> event.contains() # Does the event contain any values at all?
        False

        >>> event.add("key", "1")
        >>> event.contains()
        True
        >>> event.contains("key") # Any value for key "key"?
        True
        >>> event.contains(value="1") # Value "1" for any key?
        True
        >>> event.contains("key", "1") # Value "1" for key "key"?
        True
        >>> event.contains("other", "2") # Value "2" for key "other"?
        False

        Parsing and filtering functions can be given to modify the results.

        >>> def int_parse(string):
        ...     try:
        ...         return int(string)
        ...     except ValueError:
        ...         return None
        >>> event.contains(parser=int_parse) # Any int value for any key?
        True
        >>> event.contains("key", parser=int_parse)
        True
        >>> event.add("other", "x")
        >>> event.contains("other", parser=int_parse)
        False
        """

        if key is self._UNDEFINED:
            values = set(self._unkeyed())
        else:
            key = _normalize(key)
            values = self._attrs.get(key, ())

        if parser is not None:
            parsed = (parser(x) for x in values)

            if filter is not None:
                filtered = (x for x in parsed if filter(x))
            else:
                filtered = (x for x in parsed if x is not None)
        elif filter is not None:
            filtered = (x for x in values if filter(x))
        else:
            filtered = values

        if value is self._UNDEFINED:
            for _ in filtered:
                return True
            return False
        return value in set(values)

    def items(self, parser=None, filter=None):
        """Return a tuple of key-value pairs contained by the event.

        >>> event = Event()
        >>> event.items()
        ()
        >>> event.add("key", "1")
        >>> event.add("other", "x", "y")
        >>> sorted(event.items())
        [(u'key', u'1'), (u'other', u'x'), (u'other', u'y')]

        Parsing and filtering functions can be given to modify the results.

        >>> def int_parse(string):
        ...     try:
        ...         return int(string)
        ...     except ValueError:
        ...         return None
        >>> event.items(parser=int_parse)
        ((u'key', 1),)

        The order of the key-value pairs is undefined.
        """

        result = list()

        for key, values in self._attrs.iteritems():
            for value in values:
                if parser is not None:
                    value = parser(value)
                if filter is not None and not filter(value):
                    continue
                if filter is None and value is None:
                    continue
                result.append((key, value))

        return tuple(result)

    def keys(self, parser=None, filter=None):
        """Return a tuple of keys with at least one value.

        >>> event = Event()
        >>> event.keys()
        ()
        >>> event.add("key", "1")
        >>> event.add("other", "x", "y")
        >>> sorted(event.keys())
        [u'key', u'other']

        Parsing and filtering functions can be given to modify the
        results.

        >>> def int_parse(string):
        ...     try:
        ...         return int(string)
        ...     except ValueError:
        ...         return None
        >>> sorted(event.keys(parser=int_parse))
        [u'key']
        """

        return tuple(key for key in self._attrs
                     if self.contains(key, parser=parser, filter=filter))

    def to_elements(self, include_body=True):
        event = Element("event", xmlns=EVENT_NS)

        for key, value in self.items():
            key = _escape(key)
            value = _escape(value)
            attr = Element("attr", key=key, value=value)
            event.add(attr)

        if not include_body:
            return event

        body = Element("body")
        body.text = unicode(self)
        return Elements(body, event)

    def __eq__(self, other):
        if not isinstance(other, Event):
            return NotImplemented
        return other._attrs == self._attrs

    def __ne__(self, other):
        value = self.__eq__(other)
        if value is NotImplemented:
            return NotImplemented
        return not value

    def __unicode__(self):
        """Return an unicode representation of the event.

        >>> e = Event()
        >>> unicode(e)
        u''
        >>> e.add("a,", "b")
        >>> unicode(e)
        u'"a,"=b'

        The specific order of the key-value pairs is undefined.
        """

        return u", ".join(_unicode_quote(key) + u"=" + _unicode_quote(value)
                          for (key, value) in self.items())

    def __repr__(self):
        attrs = dict()
        for key, value in self.items():
            attrs.setdefault(key, list()).append(value)
        return self.__class__.__name__ + "(" + repr(attrs) + ")"

def stanzas_to_events():
    return idiokit.map(Event.from_elements)

def events_to_elements():
    return idiokit.map(lambda x: (x.to_elements(),))

class EventCollector(object):
    def __init__(self, compresslevel=6):
        self.stringio = StringIO()
        self.compresslevel = compresslevel
        self.gz = gzip.GzipFile(None, "w", compresslevel, self.stringio)

    def __setstate__(self, (compresslevel, data)):
        self.stringio = StringIO()
        self.stringio.write(data)
        self.compresslevel = compresslevel
        self.gz = gzip.GzipFile(None, "a", compresslevel, self.stringio)

    def __getstate__(self):
        self.gz.flush()
        self.gz.close()
        state = self.compresslevel, self.stringio.getvalue()
        self.stringio.close()
        self.__setstate__(state)
        return state

    def append(self, event):
        attrs = dict()
        for key, value in event.items():
            attrs.setdefault(key, list()).append(value)
        self.gz.write(repr(attrs) + os.linesep)

    def purge(self):
        """
        >>> collector = EventCollector()

        >>> event = Event()
        >>> event.add("1", "2")
        >>> collector.append(event)

        >>> event2 = Event()
        >>> event2.add("x", "y")
        >>> collector.append(event2)

        >>> collector.append(event)
        >>> list(collector.purge()) == [event, event2, event]
        True
        """

        stringio = self.stringio
        self.stringio = StringIO()

        self.gz.flush()
        self.gz.close()
        self.gz = gzip.GzipFile(None, "w", 6, self.stringio)

        return EventList(stringio)

class EventList(object):
    def __init__(self, stringio=None):
        self.stringio = stringio
        self.extensions = list()

    def __iter__(self):
        if self.stringio is not None:
            seek = self.stringio.seek
            tell = self.stringio.tell

            seek(0)
            gz = gzip.GzipFile(fileobj=self.stringio)

            try:
                for line in gz:
                    event = Event()
                    for key, values in eval(line).items():
                        event.update(key, values)
                    pos = tell()
                    yield event
                    seek(pos)
            finally:
                gz.close()

        for other in self.extensions:
            for event in other:
                yield event

    def extend(self, other):
        self.extensions.append(other)

    def __nonzero__(self):
        for _ in self:
            return True
        return False

if __name__ == "__main__":
    import doctest
    doctest.testmod()
