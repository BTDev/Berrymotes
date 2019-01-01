const messageHandlers = {
    postprocess: doPostprocess,
    disposeEmote: doDisposeEmote
}

self.addEventListener("message", async ({data}) => {
    const handler = messageHandlers[data.type]
    try {
        let result

        if (handler)
            result = await handler(data)
        else
            throw new Error(`Invalid message type ${data.type}`)

        self.postMessage({
            type: data.type,
            result,
            requestId: data.requestId
        })
    } catch (e) {
        self.postMessage({
            type: data.type,
            error: e.stack || "fatal error",
            requestId: data.requestId
        })
    }
})

const postprocessCache = {}
const dataUrlToCacheKey = {}

async function doPostprocess({emote, postprocess: {speed}}) {
    const cacheKey = `emote:${emote.id},speed:${speed}`
    if (postprocessCache.hasOwnProperty(cacheKey)) {
        const cached = postprocessCache[cacheKey]
        cached.refcount++
        return {dataUrl: cached.dataUrl, didChange: true}
    }
    
    const url = emote["apng_url"] || emote["background-image"]
    const response = await fetch(url)

    if (!response.ok)
        throw new Error(`Bad response: ${response.status}`)

    let buffer = await response.arrayBuffer()
    let didChange = false

    if (emote.hasOwnProperty("apng_url")) {
        if (speed) {
            try {
                [didChange, buffer] = setApngSpeed(buffer, speed)
            } catch (e) {
                console.error(e)
            }
        }
    }

    if (!didChange)
        return {didChange}

    const dataUrl = URL.createObjectURL(new Blob([buffer]))
    
    postprocessCache[cacheKey] = {
        refcount: 1, 
        dataUrl
    }    

    dataUrlToCacheKey[dataUrl] = cacheKey

    return {
        dataUrl,
        didChange
    }
}

async function doDisposeEmote({dataUrl}) {
    if (!dataUrlToCacheKey.hasOwnProperty(dataUrl)) {
        URL.revokeObjectURL(dataUrl)
        return
    }

    const cacheKey = dataUrlToCacheKey[dataUrl]
    if (!postprocessCache.hasOwnProperty(cacheKey)) {
        delete dataUrlToCacheKey[dataUrl]
        URL.revokeObjectURL(dataUrl)
        return
    }
    
    const cached = postprocessCache[cacheKey]
    
    if (cached.refcount == 1) {
        delete dataUrlToCacheKey[dataUrl]
        delete postprocessCache[cacheKey]
        URL.revokeObjectURL(dataUrl)
    } else
        cached.refcount--
}

const apngHeader = [137, 80, 78, 71, 13, 10, 26, 10]
function setApngSpeed(buffer, speed) {
    if (speed == 0)
        return [false, buffer]

    speed = 1 / speed

    const view = new Uint8Array(buffer)
    const data = new DataView(buffer)

    for (let i = 0; i < apngHeader.length; i++) {
        if (view[i] != apngHeader[i])
            throw new Error(`Unexpected header near byte ${i}. ${view[i]} != ${apngHeader[i]}`)
    }

    parseChunks(view, (type, _, offset, length) => {
        if (type != "fcTL")
            return

        // read about this frame here https://wiki.mozilla.org/APNG_Specification#.60fcTL.60:_The_Frame_Control_Chunk
        const delayNumerator = readWord(view, offset + 8 + 20) || 1
        const delayDenomerator = readWord(view, offset + 8 + 22) || 1

        const newDelay = Math.max(11, Math.round((delayNumerator / delayDenomerator) * speed * 1000))
        data.setUint16(offset + 8 + 20, newDelay)
        data.setUint16(offset + 8 + 22, 1000)

        // don't forget to re-caulate the CRC! Firefox doesn't seem to care, but Chrome will not render the apng at
        // all if any frames have a wrong checksum.
        const newCrc = calculateCrc32(view.slice(offset + 4, offset + 4 + 4 + length))
        data.setUint32(offset + length + 4 + 4, newCrc)
    })

    return [true, buffer]
}

// for performance
// taken from https://gist.github.com/jonleighton/958841
function base64ArrayBuffer(arrayBuffer) {
    var base64 = ''
    var encodings = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

    var bytes = new Uint8Array(arrayBuffer)
    var byteLength = bytes.byteLength
    var byteRemainder = byteLength % 3
    var mainLength = byteLength - byteRemainder

    var a, b, c, d
    var chunk

    // Main loop deals with bytes in chunks of 3
    for (var i = 0; i < mainLength; i = i + 3) {
        // Combine the three bytes into a single integer
        chunk = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2]

        // Use bitmasks to extract 6-bit segments from the triplet
        a = (chunk & 16515072) >> 18 // 16515072 = (2^6 - 1) << 18
        b = (chunk & 258048) >> 12 // 258048   = (2^6 - 1) << 12
        c = (chunk & 4032) >> 6 // 4032     = (2^6 - 1) << 6
        d = chunk & 63               // 63       = 2^6 - 1

        // Convert the raw binary segments to the appropriate ASCII encoding
        base64 += encodings[a] + encodings[b] + encodings[c] + encodings[d]
    }

    // Deal with the remaining bytes and padding
    if (byteRemainder == 1) {
        chunk = bytes[mainLength]

        a = (chunk & 252) >> 2 // 252 = (2^6 - 1) << 2

        // Set the 4 least significant bits to zero
        b = (chunk & 3) << 4 // 3   = 2^2 - 1

        base64 += encodings[a] + encodings[b] + '=='
    } else if (byteRemainder == 2) {
        chunk = (bytes[mainLength] << 8) | bytes[mainLength + 1]

        a = (chunk & 64512) >> 10 // 64512 = (2^6 - 1) << 10
        b = (chunk & 1008) >> 4 // 1008  = (2^6 - 1) << 4

        // Set the 2 least significant bits to zero
        c = (chunk & 15) << 2 // 15    = 2^4 - 1

        base64 += encodings[a] + encodings[b] + encodings[c] + '='
    }

    return base64
}

function getGreatedCommongDenom(a, b) {
    return b < 0.0000001
        ? a
        : getGreatedCommongDenom(b, Math.floor(a % b))
}

function convertDecimalToFraction(decimal) {
    const len = decimal.toString().length - 2
    const denominator = Math.pow(10, len)
    const numerator = decimal * denominator
    const divisor = getGreatedCommongDenom(numerator, denominator)
    return [numerator / divisor, denominator / divisor]
}

// the following helper functions are taken from
// https://github.com/davidmz/apng-canvas/blob/master/src/parser.js

/**
 * @param {Uint8Array} bytes
 * @param {function(string, Uint8Array, int, int)} callback
 */
function parseChunks(bytes, callback) {
    var off = 8;
    do {
        var length = readDWord(bytes, off);
        var type = readString(bytes, off + 4, 4);
        var res = callback(type, bytes, off, length);
        off += 12 + length;
    } while (res !== false && type != "IEND" && off < bytes.length);
}

/**
 * @param {Uint8Array} bytes
 * @param {int} off
 * @return {int}
 */
function readDWord(bytes, off) {
    var x = 0;
    // Force the most-significant byte to unsigned.
    x += ((bytes[0 + off] << 24) >>> 0);
    for (var i = 1; i < 4; i++) x += ((bytes[i + off] << ((3 - i) * 8)));
    return x;
}

/**
 * @param {Uint8Array} bytes
 * @param {int} off
 * @return {int}
 */
function readWord(bytes, off) {
    var x = 0;
    for (var i = 0; i < 2; i++) x += (bytes[i + off] << ((1 - i) * 8));
    return x;
}

function readString(bytes, off, length) {
    var chars = Array.prototype.slice.call(bytes.subarray(off, off + length));
    return String.fromCharCode.apply(String, chars);
}

// taken from https://stackoverflow.com/a/18639903
// crc32b
// Example input        : [97, 98, 99, 100, 101] (Uint8Array)
// Example output       : 2240272485 (Uint32)
function calculateCrc32(data) {
    let table = calculateCrc32._table;

    if (!table) {
        table = new Uint32Array(256);

        // Pre-generate crc32 polynomial lookup table
        // http://wiki.osdev.org/CRC32#Building_the_Lookup_Table
        // ... Actually use Alex's because it generates the correct bit order
        //     so no need for the reversal function
        for (var i = 256; i--;) {
            var tmp = i;
    
            for (var k = 8; k--;) {
                tmp = tmp & 1 ? 3988292384 ^ tmp >>> 1 : tmp >>> 1;
            }
    
            table[i] = tmp;
        }

        calculateCrc32._table = table
    }

    var crc = -1; // Begin with all bits set ( 0xffffffff )
    for (var i = 0, l = data.length; i < l; i++)
        crc = crc >>> 8 ^ table[crc & 255 ^ data[i]];

    return (crc ^ -1) >>> 0; // Apply binary NOT
}