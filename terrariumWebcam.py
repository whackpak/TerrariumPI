# -*- coding: utf-8 -*-
import logging
logger = logging.getLogger(__name__)

from time import time
from io import BytesIO
import StringIO

from picamera import PiCamera, PiCameraError
import cv2
from PIL import Image, ImageDraw, ImageFont

from hashlib import md5
import math
from datetime import datetime

import urllib2
import base64

from gevent import monkey, sleep
monkey.patch_all()

class terrariumWebcam():

  OFFLINE = 'offline'
  ONLINE = 'online'

  def __init__(self, id, location, name = '', rotation = None):
    self.id = id
    self.type = None

    # Main config
    self.tile_size = 256 # Smaller tile sizes does not work with LeafJS
    self.tile_location = 'webcam/'
    self.font_size = 10
    self.retries = 3
    self.webcam_warm_up = 2

    # Per webcam config
    self.set_location(location)
    self.set_name(name)
    self.set_rotation(rotation)

    # Variables per webcam
    self.max_zoom = 0
    self.raw_image = None
    self.resolution = None
    self.last_update = None
    self.state = None

    if self.id is None:
      self.id = md5(b'' + self.get_location()).hexdigest()
    else:
      self.id = id

    logger.info('Initialized %s webcam \'%s\' on location %s' %
                (self.get_type(),
                 self.get_name(),
                 self.get_location()))

    self.update()

  def __get_raw_image(self):
    logger.debug('Start getting raw image data from location: %s' % (self.location,))
    stream = BytesIO()
    oldstate = self.state

    for trying in range(0,self.retries):
      if 'rpicam' == self.get_type():
        stream = self.__get_raw_image_rpicam()

      elif 'usb' == self.get_type():
        stream = self.__get_raw_image_usb()

      elif 'online' == self.get_type():
        stream = self.__get_raw_image_url(stream)

      if not self.state:
        logger.warning('Attempt %s of %s for getting raw for %s type \'%s\' did not succeed at location %s. Will retry in 1 second.' %
                       (trying+1,
                        self.retries,
                        self.get_type(),
                        self.get_name(),
                        self.get_location()))
        sleep(1)
      else:
        break

    if not self.state and oldstate:
      # Changed from online to offline
      logger.warning('Raw image \'%s\' at location %s is not available!' % (self.get_name(),self.get_location(),))
      self.__get_offline_image()
      self.__tile_image()
    elif self.state:
      # "Rewind" the stream to the beginning so we can read its content
      logger.debug('Resetting raw image %s' % (self.get_name(),))
      stream.seek(0)
      # Store image in memory for further processing
      self.raw_image = Image.open(stream)
      logger.debug('Loaded raw image %s to memory' % (self.get_name(),))

      # Rotate image if needed
      if '90' == self.get_rotation():
        self.raw_image = self.raw_image.transpose(Image.ROTATE_90)
      elif  '180' == self.get_rotation():
        self.raw_image = self.raw_image.transpose(Image.ROTATE_180)
      elif '270' == self.get_rotation():
        self.raw_image = self.raw_image.transpose(Image.ROTATE_270)
      elif 'h' == self.get_rotation():
        self.raw_image = self.raw_image.transpose(Image.FLIP_TOP_BOTTOM)
      elif 'v' == self.get_rotation():
        self.raw_image = self.raw_image.transpose(Image.FLIP_LEFT_RIGHT)
      logger.debug('Rotated raw image %s to %s' % (self.get_name(),self.get_rotation()))

      self.raw_image.save(self.get_raw_image(),'jpeg',quality=95)
      logger.debug('Saved raw image %s to disk: %s' % (self.get_name(),self.get_raw_image()))

    self.last_update = int(time())

  def __get_raw_image_rpicam(self):
    logger.debug('Using RPICAM')
    stream = BytesIO()
    try:
      with PiCamera(resolution=(1920, 1080)) as camera:
        logger.debug('Open rpicam')
        camera.start_preview()
        logger.debug('Wait %s seconds for preview' % (self.webcam_warm_up,))
        sleep(self.webcam_warm_up)
        logger.debug('Save rpicam to jpeg')
        camera.capture(stream, format='jpeg')
        logger.debug('Done creating RPICAM image')
        self.state = True
    except PiCameraError, err:
      logging.error('Error getting raw RPI image from webcam \'%s\' with error message: %s' % (self.get_name(), err,))
      self.state = False

    return stream

  def __get_raw_image_usb(self):
    logger.debug('Using USB device: %s' % (self.location,))
    readok = False
    stream = StringIO.StringIO()

    try:
      logger.debug('Open USB')
      camera = cv2.VideoCapture(int(self.location[10:]))
      logger.debug('Set USB height to 1280')
      camera.set(cv2.cv.CV_CAP_PROP_FRAME_WIDTH, 1280)
      logger.debug('Set USB width to 720')
      camera.set(cv2.cv.CV_CAP_PROP_FRAME_HEIGHT, 720)
      logger.debug('Wait 2 seconds for preview')
      sleep(self.webcam_warm_up)
      logger.debug('Save USB to raw data')
      readok, image = camera.read()

      if readok:
        logger.debug('Save USB to jpeg')
        jpeg = Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
        jpeg.save(stream,'JPEG')
        logger.debug('Done creating USB image')

      logger.debug('Release USB camera')
      camera.release()
      logger.debug('Done release USB camera')
      self.state = readok
    except Exception, err:
      logging.error('Error getting raw USB image from webcam \'%s\' with error message: %s' % (self.get_name(), err,))
      self.state = False

    return stream

  def __get_raw_image_url(self,stream):
    logger.debug('Using URL: %s' % (self.location,))
    stream = StringIO.StringIO()
    try:
      if '@' in self.location:
        start = self.location.find('://') + 3
        end = self.location.find('@', start)
        auth = self.location[start:end]
        webcamurl = urllib2.Request(self.location.replace(auth+'@',''))
        auth = auth.split(':')
        base64string = base64.encodestring('%s:%s' % (auth[0], auth[1])).replace('\n', '')
        webcamurl.add_header("Authorization", "Basic %s" % base64string)
      else:
        webcamurl = urllib2.Request(self.location)

      stream = StringIO.StringIO(urllib2.urlopen(webcamurl,None,15).read())
      self.state = True
    except Exception, err:
      logging.error('Error getting raw online image from webcam \'%s\' with error message: %s' % (self.get_name(), err,))
      self.state = False

    return stream

  def __get_offline_image(self):

    def draw_text_center(im, draw, text, font, **kwargs):
      text_height = text_top = None
      linecounter = 0
      for line in text:
        text_size = draw.textsize(line, font)
        if text_height is None:
          text_height = len(text) * ( text_size[1])
          text_top = (im.size[1] - text_height) / 2

        draw.text(
          ((im.size[0] - text_size[0]) / 2, (text_top + (linecounter * text_height)) / 2),
          line, font=font, **kwargs)

        linecounter += 1

    self.raw_image = Image.open('static/images/webcam_offline.png')

    mask = Image.open('static/images/mask_offline.png')
    draw = ImageDraw.Draw(mask)
    font = ImageFont.truetype('fonts/DejaVuSans.ttf',40)
    text = ['Offline since:',datetime.now().strftime("%A %d %B %Y"),datetime.now().strftime("%H:%M:%S")]
    draw_text_center(mask,draw,text,font)

    mask_width, mask_height = mask.size
    source_width, source_height = self.raw_image.size

    self.raw_image.paste(mask, ((source_width/2)-(mask_width/2),(source_height/2)-(mask_height/2)), mask)

  def __set_timestamp(self,image):
    # Get the image dimensions
    source_width, source_height = image.size
    # Select font
    font = ImageFont.truetype('fonts/DejaVuSans.ttf',self.font_size)
    # Draw on image
    draw = ImageDraw.Draw(image)
    # Create black box on the bottom of the image
    draw.rectangle([0,source_height-(self.font_size+2),source_width,source_height],fill='black')
    # Draw the current time stamp on the image
    draw.text((1, source_height-(self.font_size+1)), self.name + ' @ ' + (datetime.now()).strftime("%d/%m/%Y %H:%M:%S") ,(255,255,255),font=font)
    del draw

  def __tile_image(self):
    starttime = time()
    # Original width
    source_width, source_height = self.raw_image.size
    self.resolution = self.raw_image.size

    # Calc new square canvas size
    longest_side = float(source_width if source_width > source_height else source_height)
    max_size = float(math.pow(2,math.ceil(math.log(longest_side,2))))

    # Set canvas dimensions
    canvas_width = canvas_height = max_size
    resize_factor = max_size / longest_side
    # Set raw image new dimensions
    source_width *= resize_factor
    source_height *= resize_factor

    # Calculate the max zoom factor
    zoom_factor = int(math.log(max_size/self.tile_size,2))
    self.max_zoom = zoom_factor
    logger.debug('Tiling image with source resolution %s, from %sx%s with resize factor %s in %s steps' %
                  (self.resolution, source_width,source_height,resize_factor, zoom_factor))

    # as long as there is a new layer, continue
    while zoom_factor >= 0:
      # Create black canvas on zoom factor dimensions
      logger.debug('Creating new black canvas with dimensions %sx%s' % (canvas_width,canvas_height))
      canvas = Image.new("RGB", ((int(round(canvas_width)),int(round(canvas_height)))), "black")
      # Scale the raw image to the zoomfactor dimensions
      logger.debug('Scale raw image to new canvas size (%sx%s)' % (canvas_width,canvas_height))
      source = self.raw_image.resize((int(round(source_width)),int(round(source_height))))
      # Set the timestamp on resized image
      self.__set_timestamp(source)

      # Calculate the center in the canvas for pasting raw image
      paste_center_position = (int(round((canvas_width - source_width) / 2)),int(round((canvas_height - source_height) / 2)))
      logger.debug('Pasting resized image to center of canvas at position %s' % (paste_center_position,))
      canvas.paste(source,paste_center_position)

      # Loop over the canvas to create the tiles
      logger.debug('Creating the lose tiles with dimensions %sx%s' % (canvas_width, canvas_height,))
      for row in xrange(0,int(math.ceil(canvas_height/self.tile_size))):
        for column in xrange(0,int(math.ceil(canvas_width/self.tile_size))):
          crop_size = ( int(row*self.tile_size), int(column*self.tile_size) ,int((row+1)*self.tile_size), int((column+1)*self.tile_size))
          logger.debug('Cropping image from position %s' % (crop_size,))
          tile = canvas.crop(crop_size)
          logger.debug('Saving cropped image to %s' % (self.tile_location + self.id + '_tile_' + str(zoom_factor) + '_' + str(row) + '_' + str(column) + '.jpg',))
          tile.save(self.tile_location + self.id + '_tile_' + str(zoom_factor) + '_' + str(row) + '_' + str(column) + '.jpg','jpeg',quality=95)
          logger.debug('Done saving %s' % (self.tile_location + self.id + '_tile_' + str(zoom_factor) + '_' + str(row) + '_' + str(column) + '.jpg',))

      # Scale down by 50%
      canvas_width /= 2.0
      canvas_height /= 2.0
      source_width /= 2.0
      source_height /= 2.0
      zoom_factor -= 1

    # Clear memory, not sure if needed
    del source
    del canvas
    logger.debug('Done tiling webcam image \'%s\' in %.5f seconds' % (self.get_name(),time()-starttime))

  def update(self):
    starttime = time()
    logger.info('Updating webcam \'%s\' at location %s' % (self.get_name(), self.get_location(),))
    self.__get_raw_image()
    if self.get_state() == 'online':
      self.__tile_image()
    logger.info('Done updating webcam \'%s\' at location %s in %.5f seconds' % (self.get_name(), self.get_location(),time()-starttime))

  def get_data(self):
    return {'id': self.get_id(),
            'location': self.get_location(),
            'name': self.get_name(),
            'rotation' : self.get_rotation(),
            'resolution': self.get_resolution(),
            'max_zoom' : self.get_max_zoom(),
            'state' : self.get_state(),
            'last_update' : self.get_last_update(),
            'image': self.get_raw_image(),
            'preview': self.get_preview_image()
            }

  def get_id(self):
    return self.id

  def get_name(self):
    return self.name

  def set_name(self,name):
    self.name = name

  def get_location(self):
    return self.location

  def set_location(self,location):
    if 'rpicam' == location:
      self.location = location
      self.type = 'rpicam'
    elif location.startswith('/dev/video'):
      self.location = location
      self.type = 'usb'
    elif self.location.startswith('http://') or self.location.startswith('https://'):
      self.location = location
      self.type = 'online'

  def get_type(self):
    return self.type

  def get_rotation(self):
    return self.rotation

  def set_rotation(self,rotation):
    self.rotation = rotation

  def get_resolution(self):
    return self.resolution

  def get_max_zoom(self):
    return self.max_zoom

  def get_state(self):
    return terrariumWebcam.ONLINE if self.state else terrariumWebcam.OFFLINE

  def get_last_update(self):
    return self.last_update

  def get_raw_image(self):
    return self.tile_location + self.get_id() + '_raw.jpg'

  def get_preview_image(self):
    return self.tile_location + self.get_id() + '_tile_0_0_0.jpg'
