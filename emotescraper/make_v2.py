#!/usr/bin/env python2
import os
import json

def from_data(emotes):
    for i, emote in enumerate(emotes):
        emote['id'] = i
        try:
            emote['tags'].remove('')
        except ValueError:
            pass
        emote['tags'].sort()

    with open(os.path.join('..', 'data', 'berrymotes_json_data.v2.json'), 'wb') as fh:
        fh.write(json.dumps(emotes, separators=(',', ':')))

def from_file(fname):
    with open(fname, 'rb') as fh:
        from_data(json.load(fh))

if __name__ == '__main__':
    from_file(os.path.join('..', 'data', 'berrymotes_json_data.json'))
