import datetime


def json_serial(obj):
    """JSON serializer for objects not serializable by default (e.g. datetime/date)."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError("Type %s not serializable" % type(obj))
