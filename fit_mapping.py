#!/usr/bin/python
# Copyright 2011 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pylab import *
import sys
import itertools

from sim_stereo import distance_from_disparity

import mpl_toolkits.mplot3d.axes3d as p3

from color_block import gucci_dict

import pdb

class IntrinsicParameters:
  def __init__(self, f, center):
    self.f = f
    self.center = center

  ## The magical formula that gives distance form the disparity. This is the
  ## theoretical perfect model, a x**-1 expression.
  def distance_from_disparity(self, d):
    return distance_from_disparity(d)

  def coordinates_from_disparity(self, disparity):
    ## Calculate the world coordinates of each pixel.

    ## Initialize the output matrix with pixel coordinates over image plane, on
    ## camera reference frame.
    output = zeros((disparity.shape[0]*disparity.shape[1], 3))
    output[:,:2] = mgrid[:disparity.shape[1],:disparity.shape[0]].T.reshape(-1,2) - self.center
    output[:,2] = self.f

    ## Calculate z from disparity
    z = self.distance_from_disparity(disparity.ravel())

    #pdb.set_trace()
    output[:,0] *= z / self.f
    output[:,1] *= z / self.f
    output[:,2] = z
    return output

class SquareMesh:
  def __init__(self, disparity, intparam):
    self.disparity = disparity
    self.intparam = intparam
    Np = self.disparity.shape[0]*self.disparity.shape[1]

  def generate_xyz_mesh(self):
    ## Calculate the coordinate values.
    self.xyz = self.intparam.coordinates_from_disparity(self.disparity)

    ## Calculate the connections.
    Nl,Nk = self.disparity.shape
    Ncon = 4 * (Nk - 1) * (Nl - 1) + Nk + Nl - 2
    self.con = zeros((Ncon,2), dtype=uint16)

    ## Loop through every pixel. Add connections when possible. Just either the
    ## same-line pixel to the right, or any of the three 8-neighbours below.
    i=0
    for p in range(Nl*Nk):
      ## If it's not in the last column, connect to right.
      if (p + 1) % Nk:
        self.con[i,0] = p
        self.con[i,1] = p+1
        i += 1
      ## If it not in the last line
      if p <  Nk * (Nl - 1):
        ## If it's not in the first column, connect to lower right.
        # if p % Nk:
        #   self.con[i,0] = p
        #   self.con[i,1] = p+Nk-1
        #   i += 1
        self.con[i,0] = p
        self.con[i,1] = p+Nk
        i += 1
        ## If it's not in the last column, connect to lower right.
        if (p + 1) % Nk:
          self.con[i,0] = p
          self.con[i,1] = p+Nk+1
          i += 1







if __name__ == '__main__':

  ion() ## Turn on real-time plotting

  ## Plot stuff or not?
  do_plot = True

  register_cmap(name='guc', data=gucci_dict)
  rc('image', cmap='guc')
  # rc('image', cmap='RdBu')

  ## Check number of parameters
  if len(sys.argv)<2:
    raise Exception('''Incorrect number of parameters.

Usage: %s <data_path>'''%(sys.argv[0]))

  ## Get the name of directory that contains the data. It should contain two
  ## files named 'params.txt' and 'disparity.txt'.
  data_path = '%s/'%(sys.argv[1])

  ## Load the image with the disparity values. E.g., the range data produced by Kinect.
  disparity = loadtxt(data_path+'disparity.txt')
  ## Load the file with the camera parameters used to render the scene
  ## The values are: [f, p[0], p[1], p[2], theta, phi, psi, k]
  params_file = loadtxt(data_path+'params.txt')
  ## The optical center is another important intrinsic parameter, but the
  ## current simulator just pretend this is not an issue. So the optical center
  ## is just the middle of the image, and there is also no radial lens
  ## distortion.
  optical_center = .5*(1+array([disparity.shape[1], disparity.shape[0]]))
  ## Focal distance
  f = params_file[0]

  ## scale down the image 6 times
  disparity = disparity[::6,::6]
  f = f/6
  optical_center = optical_center/6

  ## Instantiate intrinsic parameters object.
  mypar = IntrinsicParameters(f, optical_center)
  ## Instantiate mesh object, and calculate grid parameters in 3D from the
  ## disparity array and intrinsic parameters.
  sqmesh = SquareMesh(disparity, mypar)
  sqmesh.generate_xyz_mesh()

  x,y,z = sqmesh.xyz.T
  x = x.reshape(disparity.shape)
  y = y.reshape(disparity.shape)
  z = z.reshape(disparity.shape)

  # P = 6 ## 6 for trig-00
  # x = x[::P,::P]
  # y = y[::P,::P]
  # z = z[::P,::P]

  #############################################################################
  ## Plot the disparity as an image
  if do_plot:
    fig = plt.figure(figsize=plt.figaspect(.5))
    fig.suptitle('Calculation of 3D coordinates from range data (with quantization)', fontsize=20, fontweight='bold')
    ax = fig.add_subplot(1,2,1)
    title('Kinect data (disparity)', fontsize=16)
    cax = ax.imshow(disparity, interpolation='nearest')
    colorbar(cax, shrink=.5)

    ax = p3.Axes3D(fig, rect = [.55, .2, .4, .6], aspect='equal')
    title('Square mesh on 3D space', fontsize=16)

    ## Plot the disparity as an image

    ax.axis('equal')
    ax.plot_wireframe(x,y,z)

    mrang = max([x.max()-x.min(), y.max()-y.min(), z.max()-z.min()])/2
    midx = (x.max()+x.min())/2
    midy = (y.max()+y.min())/2
    midz = (z.max()+z.min())/2
    ax.set_xlim3d(midx-mrang, midx+mrang)
    ax.set_ylim3d(midy-mrang, midy+mrang)
    ax.set_zlim3d(midz-mrang, midz+mrang)
  ##
  #############################################################################

    figure(2)
    for p in sqmesh.con:
      plot(sqmesh.xyz[p,0], sqmesh.xyz[p,1], 'b-')