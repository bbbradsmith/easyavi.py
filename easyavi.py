#!/usr/bin/env python3
#
# easyavi.py
# Version 2
# Brad Smith, 2019
# http://rainwarrior.ca
#
# Simple way to write an AVI from a series of PIL images.
# Public domain.

# Example usage:
#
# import easyavi
# a = easyavi.open("example.avi", 640, 480, 30) # filename, width, height, FPS
# a.write(frame0) # frame0 is a PIL.Image
# a.write(frame1)
# a.write(frame2)
# ...
# a.close()
#
# easyavi.open() with parameter rle=True will use RLE encoding,
# which is drastically smaller for images with rows of flat colour,
# or sequences where only part of the image changes.
# The encoding, however, is fairly slow.

# Notes:
#
#   Tested with PIL 5.4.1
#
#   AVI is uncompressed RGB. File size will be large.
#   open() with rle=True will use RLE encoding, which can be much smaller, but is slower.
#
#   File size is limited to 4GB. Some players will have trouble over 2GB or possibly 1GB.
#   The .write() function returns the current file position. You may wish to monitor
#   this, and then .close() and reopen a new file to continue when it approaches the
#   file size you wish to avoid. Leave some headroom, as .close() needs to store a
#   data suffix with indices. Proper long file support would require OpenDML index chunks,
#   which seemed too complex to be worthwhile.


import sys
assert sys.version_info[0] == 3, "Python 3 required."

import PIL.Image
import struct
import builtins

class EasyAvi:

    KEYFRAME_TIME = 10 # seconds per keyframe in RLE mode

    def write_fcc(self,name):
        assert(len(name)==4)
        self.f.write(name.encode("ASCII"))

    def make_fixup(self):
        pos = self.f.tell()
        self.f.write(struct.pack("<L",0)) # placeholder
        return pos

    def push_riff(self,fcc):
        self.write_fcc(fcc)
        self.riff_fixup.append(self.make_fixup())

    def pop_riff(self):
        fixup = self.riff_fixup.pop()
        chunk_size = self.f.tell() - (fixup + 4)
        self.f.seek(fixup,0)
        self.f.write(struct.pack("<L",chunk_size))
        self.f.seek(0,2) # return to end

    def write_prefix(self):
        assert(self.open)
        assert(len(self.riff_fixup) == 0) # top level
        frame_size = self.w * self.h * 3
        frame_extra = (frame_size//64) if self.rle else 0 # overhead for worst case compression failure
        # open AVI RIFF list
        self.push_riff("RIFF")
        self.write_fcc("AVI ")
        # open hdrl list (header chunks)
        self.push_riff("LIST")
        self.write_fcc("hdrl")
        # avih chunk (main AVI header)
        self.push_riff("avih")
        self.f.write(struct.pack("<LLLL",
            1000000//self.fps, # us/frame
            frame_size * self.fps, # max bytes per second
            0, # padding
            0x10)) # flags (AVIF_HASINDEX)
        self.frames_fixup.append(self.make_fixup())
        self.f.write(struct.pack("<LLLLLLLLL",
            0, # initial frames (audio delay)
            1, # number of streams
            frame_size, # bytes per frame
            self.w,
            self.h,
            0,0,0,0)) # reserved
        self.pop_riff()
        # open strl list (stream description)
        self.push_riff("LIST")
        self.write_fcc("strl")
        # strh chunk (stream header)
        self.push_riff("strh")
        self.write_fcc("vids")
        self.write_fcc("RLE " if self.rle else "MSVC")
        self.f.write(struct.pack("<LHHLLLL",
            0, # flags
            0, # priority
            0, # language
            0, # initial frames (audio delay)
            1, # rate divisor
            self.fps, # rate divisor
            0)) # start
        self.frames_fixup.append(self.make_fixup())
        self.f.write(struct.pack("<LlLHHHH",
            frame_size + frame_extra, # suggested chunk buffer size
            -1, # quality
            0, # sample size
            0,0,self.w,self.h)) # rectangle
        self.pop_riff()
        # strf chunk (stream format, BITMAPINFOHEADER)
        self.push_riff("strf")
        self.f.write(struct.pack("<LllHHLLllLL",
            40, # size of structure
            self.w,
            self.h,
            1, # planes
            24, # bit depth
            1 if self.rle else 0, # BI_RLE or BI_RGB
            0, # size of image
            0,0, # pixels per metre in display
            0,0)) # palette colours used, important palette colours
        self.pop_riff()
        # close strl list
        self.pop_riff()
        # close hdrl list
        self.pop_riff()
        # open movi list (video chunks)
        self.push_riff("LIST")
        self.write_fcc("movi")
        # now ready to create frame chunks
        # RIFF AVI list, and movi list are currently open
        self.index_pos = 4

    def write_suffix(self):
        assert(self.open)
        # close movi list
        self.pop_riff()
        # idx1 chunk
        self.push_riff("idx1")
        assert(len(self.indices) == self.frames)
        for idx in self.indices:
            self.write_fcc(idx[0])
            self.f.write(struct.pack("<LLL",idx[1],idx[2],idx[3]))
        self.pop_riff()
        self.indices = []
        # close RIFF AVI list
        self.pop_riff()
        assert(len(self.riff_fixup) == 0) # returned to top level
        # fix up frame count
        for pos in self.frames_fixup:
            self.f.seek(pos,0)
            self.f.write(struct.pack("<L",self.frames))
        self.f.seek(0,2) # return to end
        self.frames_fixup = []       

    def write_frame_chunk(self,fcc,flags,data):
        self.write_fcc(fcc)
        self.f.write(struct.pack("<L",len(data)))
        self.f.write(data)
        index = (fcc, flags, self.index_pos, len(data))
        self.index_pos += len(data) + 8
        return index

    # uncompressed BGR24 encoder

    def write_frame_raw(self,img):
        stride = img.width * 3
        if stride & 3: # pad each line to 4 byte boundary
            stride += 4 - (stride & 3)
        bgr = img.tobytes("raw","BGR", stride, -1)
        assert(self.open)
        return self.write_frame_chunk("00db",0x10,bgr)

    # compressed MSRLE24 encoder

    def pixel_to_bgr(pixel):
        return [pixel[2],pixel[1],pixel[0]]
    
    def row_rle(ipixel,ppixel,w,y):
        data = []
        read = y * w # pixels already encoded
        pos = read # pixels currently investigated
        end = pos + w
        # generate absolute packet to catch up to position
        def emit_absolute():
            nonlocal pos, read, ipixel, data
            abslen = pos - read
            if abslen < 1:
                return
            if abslen < 3:
                while read < pos:
                    data.append(1)
                    data += EasyAvi.pixel_to_bgr(ipixel[read])
                    read += 1
                return
            abslen = min(abslen,255)
            data.append(0)
            data.append(abslen)
            target = read + abslen
            while read < target:
                data += EasyAvi.pixel_to_bgr(ipixel[read])
                read += 1
            #if (abslen & 1): RLE8 pads to word, but not RLE24, apparently?
            #    data.append(0)
            emit_absolute() # recurse, in case it was more than 255
        # scan through row and encode
        while (pos < end):
            # count consecutive delta pixels
            match = 0
            if not ppixel == None:
                for i in range(pos,end):
                    if ipixel[i] != ppixel[i]:
                        break
                    match += 1
            # count consecutive matching pixels
            p = ipixel[pos]
            run = 1
            for i in range(pos+1,end):
                if ipixel[i] != p:
                    break
                run += 1
            run = min(run,255)
            # decide whether to emit a match, run, or collect raw bytes for absolute encoding
            if (run > match) and (run > 1):
                emit_absolute()
                data.append(run)
                data += EasyAvi.pixel_to_bgr(ipixel[pos])
                read += run
                pos += run
            elif match > 1:
                emit_absolute()
                if (pos + match) >= end:
                    break # immediate end of line
                match = min(match,255)
                data.append(0)
                data.append(2) # skip command
                data.append(match) # X skip
                data.append(0) # Y skip
                read += match
                pos += match
            else:
                # read is left behind, will be pickedup by emit_absolute
                pos += 1
        emit_absolute() # finish any remaining pixels
        # end of line
        data.append(0)
        data.append(0)
        return data

    def write_frame_rle(self,img): # MSRLE 24
        if self.previous == None or (self.frames % self.keyrate) == 0:
            previous = None
            fcc = "00db"
            flags = 0x10 # AVIIF_KEYFRAME
        else:
            previous = self.previous
            fcc = "00dc"
            flags = 0
        data = []
        imgdata = img.getdata()
        previousdata = None if (previous == None) else previous.getdata()
        for y in range(img.height,0,-1):
            data += EasyAvi.row_rle(imgdata,previousdata,img.width,y-1)
        data = data[0:-2] + [0,1] # remove last end of line, replace with end of bitmap
        # pad to 4 byte boundary
        while (len(data) & 3):
            data.append(0)
        # retain last image for delta comparison
        if self.previous == None:
            self.previous = img.copy()
        else:
            self.previous.paste(img)
        return self.write_frame_chunk(fcc,flags,bytes(data))

    # constructor/destructor

    def __init__(self, file_handle, w, h, fps, rle):
        self.f = file_handle
        self.w = w
        self.h = h
        self.fps = fps
        self.rle = rle
        self.open = True
        self.frames = 0
        self.riff_fixup = []
        self.frames_fixup = []
        self.indices = []
        self.index_pos = 0
        self.previous = None
        self.keyrate = fps * EasyAvi.KEYFRAME_TIME
        self.write_prefix()

    def __del__(self):
        self.close()

    # public interface

    def write(self,img):
        """Writes a PIL.Image as the next frame. Returns current file length."""
        assert(img.width==self.w)
        assert(img.height==self.h)
        if not self.rle:
            index = self.write_frame_raw(img)
        else:
            index = self.write_frame_rle(img)
        self.indices.append(index)
        self.frames += 1
        return self.f.tell()

    def close(self):
        """Finishes writing to disk and closes AVI file."""
        if (self.open):
            self.write_suffix()
            self.f.close()
            self.open = False

def open(filename, w, h, fps, rle=False):
    """Opens an AVI file for writing."""
    f = builtins.open(filename,"wb")
    return EasyAvi(f,w,h,fps,rle)
