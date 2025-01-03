#!/usr/bin/env python3
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
    return emotes

def from_file(fname):
    with open(fname, 'rb') as fh:
        return from_data(json.load(fh))

if __name__ == '__main__':
    data = from_file(os.path.join('..', 'data', 'berrymotes_json_data.json'))

    with open(os.path.join('..', 'data', 'berrymotes_json_data.v2.json'), 'wb') as fh:
        fh.write(json.dumps(data, separators=(',', ':')))
