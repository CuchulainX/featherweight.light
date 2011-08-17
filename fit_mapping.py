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
from fit_cone import *
from  scipy.optimize import leastsq
import scipy.interpolate
import sys
import itertools

import Image # For the quad transformation

import mpl_toolkits.mplot3d.axes3d as p3

from color_block import gucci_dict

import pdb


def fitfunc(u, M):
  Ned = (M.shape[1]-3)/2
  R = zeros(Ned+3)
  D = dot(u,M)**2
  R[:Ned] = D[0:-3:2]+D[1:-3:2]
  R[-3:] = D[-3:]
  return R

def devfunc(u, M):
  return 2*dot(u,M)

errfunc = lambda u, M, d_x: fitfunc(u, M) - d_x


def distance_from_disparity(d):
  z = zeros(d.shape, dtype=float)
  ## "identity" version
  #return 1/(d/1e3)
  # return 3e2-1./(d/5e1) ## for cone-00
  # return 2-1./(d/5e3) ## for trig-00
  # return 1000-1/(d/1e5)
  ## Correct version, inverse of the function from http://mathnathan.com/2011/02/03/depthvsdistance/
  return 348.0 / (1091.5 - d)
  # return d


class ExtrinsicParameters:
  def __init__(self, T, R):
    self.T = T
    self.R = R
  def look_at(self,P):
    Q = P-self.T
    theta = arctan2(Q[0], Q[2])
    phi = arctan2(-Q[1], sqrt(Q[0]**2+Q[2]**2))
    psi = 0
    R_psi = array([[cos(psi), sin(psi),0],[-sin(psi), cos(psi),0],[0,0,1]])
    R_theta = array([[cos(theta), 0, -sin(theta)],[0,1,0],[sin(theta), 0, cos(theta)]])
    R_phi = array([[1,0,0],[0, cos(phi), sin(phi)],[0, -sin(phi), cos(phi)]])
    self.R = dot(dot(R_theta.T, R_phi.T), R_psi.T)
    #self.R = dot(dot(R_theta, R_phi), R_psi).T




class IntrinsicParameters:
  def __init__(self, f, center):
    self.f = f
    self.center = center

  def subsample(self, sub):
    self.f /= sub
    self.center /= sub

  def crop(self, bbox):
    self.center -= array([bbox[0], bbox[1]])

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
        ## Connect to the point below
        self.con[i,0] = p
        self.con[i,1] = p+Nk
        i += 1
        ## If it's not in the first column, connect to lower left.
        if p % Nk:
          self.con[i,0] = p
          self.con[i,1] = p+Nk-1
          i += 1
        ## If it's not in the last column, connect to lower right.
        if (p + 1) % Nk:
          self.con[i,0] = p
          self.con[i,1] = p+Nk+1
          i += 1

    ## Connections for a square emsh (mostly for plotting)
    Nsqcon = 2 * Nk * Nl - Nl -Nk
    self.sqcon = zeros((Nsqcon,2), dtype=uint16)
    ## Loop through every pixel. Add connections when possible. Just either the
    ## same-line pixel to the right, or any of the three 8-neighbours below.
    i=0
    for p in range(Nl*Nk):
      ## If it's not in the last column, connect to right.
      if (p + 1) % Nk:
        self.sqcon[i,0] = p
        self.sqcon[i,1] = p+1
        i += 1
      ## If it not in the last line
      if p <  Nk * (Nl - 1):
        ## Connect to the point below
        self.sqcon[i,0] = p
        self.sqcon[i,1] = p+Nk
        i += 1


  def subsample(self, sub):
    self.disparity = self.disparity[::sub,::sub]
    self.intparam.subsample(sub)

  def crop(self, bbox):
    self.disparity = self.disparity[bbox[1]:bbox[3],bbox[0]:bbox[2]]
    self.intparam.crop(bbox)

  def smash(self):
    ## Deal with outliers, just look for the maximum value outside of the maximum possible, then make the outliers the same.
    self.disparity[self.disparity==2047] = self.disparity[self.disparity<2047].max()

  def run_optimization(self):
    ## Find the "middle" point to make it the origin, and make it.
    self.mp = (self.disparity.shape[0]/2) * self.disparity.shape[1] + self.disparity.shape[1]/2
    ## Set the initial estimate from the original xy coordinates, subtracting by the location of the middle point
    self.u0 = reshape(self.xyz[:,:2] - self.xyz[self.mp,:2] ,-1)

    ## Start to set up optimization stuff
    Np = self.xyz.shape[0] #disparity.shape[0] * disparity.shape[1]
    Ned = self.con.shape[0]

    print Np, Ned

    M = zeros((2*Np, 2*Ned+3))
    d_x = zeros(Ned+3)

    for i in range(Ned):
      a,b = self.con[i]

      M[a*2,2*i] = 1
      M[b*2,2*i] = -1
      M[a*2+1,2*i+1] = 1
      M[b*2+1,2*i+1] = -1
      #d_x[i] = sqrt( ((self.xyz[a] - self.xyz[b]) ** 2 ).sum() )
      d_x[i] = ( ((self.xyz[a] - self.xyz[b]) ** 2 ).sum() )

    ## Find the "middle" point to make it the origin
    mp = (self.disparity.shape[0]/2) * self.disparity.shape[1] + self.disparity.shape[1]/2
    M[2*mp,-3] = 1
    M[2*mp+1,-2] = 1
    M[2*mp+3,-1] = 1

    mdist = d_x.mean()

    ## Fit this baby
    uv_opt, success = scipy.optimize.leastsq(errfunc, self.u0, args=(M, d_x,))

    final_err = (errfunc(uv_opt, M, d_x)**2).sum()

    self.uv = reshape(uv_opt,(-1,2))

    return success, final_err

  def project_into_camera(self, int_param, ext_param):
    xyz_c = dot(self.xyz - ext_param.T, ext_param.R)
    self.rs = int_param.center + int_param.f * xyz_c[:,:2] / xyz_c[:,[2,2]]




###############################################################################
##
##
if __name__ == '__main__':

  ion() ## Turn on real-time plotting

  ## Plot stuff or not?
  # plot_wireframe = True
  plot_wireframe = False
  plot_scatter = True
  # plot_scatter = False
  plot_meshes = True
  # plot_meshes = False
  plot_cam = True
  # plot_cam = False

  register_cmap(name='guc', data=gucci_dict)
  rc('image', cmap='guc')
  # rc('image', cmap='RdBu')

  ## Check number of parameters
  if len(sys.argv)<2:
    raise Exception('''Incorrect number of parameters.

Usage: %s <data_path>'''%(sys.argv[0]))

  paul_data = True

  ## Get the name of directory that contains the data. It should contain two
  ## files named 'params.txt' and 'disparity.txt'.
  data_path = '%s/'%(sys.argv[1])

  if paul_data:
    ## Load the image with the disparity values. E.g., the range data produced by Kinect.
    disparity = loadtxt(data_path+'kinect.mat')

    optical_center = .5*(1+array([disparity.shape[1], disparity.shape[0]]))
    f = 640
  else:
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

  ## Instantiate intrinsic parameters object.
  mypar = IntrinsicParameters(f, optical_center)

  ## Parameters to pre-process the image. First crop out the interest region,
  ## then downsample, then turn the outliers into more ammenable values.
  #bbox = (0, 0, disparity.shape[1], disparity.shape[0]) # whole image
  # bbox = (230, 125, 550, 375) #just the book, whole book
  bbox = (230, 125, 400, 375)
  sub = 20

  #############################################################################
  ## Instantiate mesh object, and calculate grid parameters in 3D from the
  ## disparity array and intrinsic parameters.
  sqmesh = SquareMesh(disparity, mypar)
  ## Cut the image (i.e. segment the book...)
  sqmesh.crop(bbox)
  ## Resample down the image 'sub' times, and handle outliers
  sqmesh.subsample(sub)
  sqmesh.smash()
  ## Generate the 3D point cloud and connection array
  sqmesh.generate_xyz_mesh()

  #############################################################################
  ## Run the optimization
  sqmesh.run_optimization()

  q0 = reshape(sqmesh.u0, (-1, 2)) # , reshape(u_opt,(-1,2)), final_err
  q_opt = reshape(sqmesh.uv, (-1, 2)) # , reshape(u_opt,(-1,2)), final_err

  #############################################################################
  ## Create camera projection of the 3D model
  T = array([0.05,0,-0.05])
  R = quaternion_to_matrix([0,0,0])
  cam_ext = ExtrinsicParameters(T,R)
  #cam_ext.look_at(array([-.02,-0.207,.58]))
  cam_ext.look_at(array([-.02,.03,.57]))

  cam_shot = rot90(imread(data_path+'img.png'),3)
  c_f = 86/.009 # (Lens focal length divided by pixel size, in mm)
  c_copt = array([cam_shot.shape[1]/2., cam_shot.shape[0]/2.])

  cam_int = IntrinsicParameters(c_f, c_copt)

  sqmesh.project_into_camera(cam_int, cam_ext)

  #############################################################################
  ## Calculate mapping value at grid points for mapping

  cam_shot.shape
  spacing = 300 # rs mesh ~ 300 pixels for sub = 20

  grid_r, grid_s = mgrid[0:cam_shot.shape[1]:spacing,0:cam_shot.shape[0]:spacing]

  grid_u = griddata(sqmesh.rs[:,0], sqmesh.rs[:,1], sqmesh.uv[:,0], grid_r, grid_s)
  grid_v = griddata(sqmesh.rs[:,0], sqmesh.rs[:,1], sqmesh.uv[:,1], grid_r, grid_s)

  the_mappings = []
  lims_uv = zeros(4)

  for j in range(grid_r.shape[0]-1):
    for k in range(grid_r.shape[1]-1):
      if (grid_u.mask[j,k] or grid_v.mask[j,k] or
          grid_u.mask[j,k+1] or grid_v.mask[j,k+1] or
          grid_u.mask[j+1,k] or grid_v.mask[j+1,k] or
          grid_u.mask[j+1,k+1] or grid_v.mask[j+1,k+1] ):
        print j,k, 'eek!'
        continue
      r1, s1 = grid_r[j,k], grid_s[j,k]
      r2, s2 = grid_r[j+1,k+1], grid_s[j+1,k+1]
      u1, v1 = grid_u[j,k], grid_v[j,k]
      u4, v4 = grid_u[j+1,k], grid_v[j+1,k]
      u3, v3 = grid_u[j+1,k+1], grid_v[j+1,k+1]
      u2, v2 = grid_u[j,k+1], grid_v[j,k+1]
      the_mappings.append((r1,s1,r2,s2,u1,v1,u2,v2,u3,v3,u4,v4))

      lims_uv[0] = min(lims_uv[0], u1,u2,u3,u4)
      lims_uv[1] = min(lims_uv[1], v1,v2,v3,v4)
      lims_uv[2] = max(lims_uv[2], u1,u2,u3,u4)
      lims_uv[3] = max(lims_uv[3], v1,v2,v3,v4)

  the_mappings = array(the_mappings)

  max_range_uv = max(lims_uv[2] - lims_uv[0], lims_uv[3] - lims_uv[1])
  map_scale = 2000 / max_range_uv
  output_size = (map_scale*(lims_uv[2] - lims_uv[0]), map_scale*(lims_uv[3] - lims_uv[1]))

  the_mappings[:,4::2] -= lims_uv[0]
  the_mappings[:,5::2] -= lims_uv[1]
  the_mappings[:,4:] *= map_scale*10

  im = Image.open(data_path+'img.png')
  cam_shot_pil = im.transpose(Image.ROTATE_270)

  map_list = [((a[0],a[1],a[2],a[3]), (a[4], a[5], a[6], a[7], a[8], a[9], a[10],a[11])) for a in the_mappings]


  dewarped_image = cam_shot_pil.transform(output_size, Image.MESH, map_list)

  dewarped_image.save('dewarped.png')





  #############################################################################
  ## Plot stuff
  if plot_wireframe:
    ## Plot disparity data as an image
    x,y,z = sqmesh.xyz.T

    figure()
    title('Kinect data', fontsize=20, fontweight='bold')
    #fig.suptitle('Wireframe from reconstructed kinect data', fontsize=20, fontweight='bold')
    title('Kinect data (disparity)', fontsize=16)

    dmax = disparity[disparity<2047].max()
    dmin = disparity.min()

    cax = imshow(disparity, interpolation='nearest', vmin=dmin, vmax=dmax)
    colorbar(cax, shrink=.5)

    ## Plot wireframe
    fig = figure()
    ax = p3.Axes3D(fig, aspect='equal')
    title('Square mesh on 3D space', fontsize=20, fontweight='bold')

    ax.axis('equal')
    ax.plot_wireframe(x,y,z)

    mrang = max([x.max()-x.min(), y.max()-y.min(), z.max()-z.min()])/2
    midx = (x.max()+x.min())/2
    midy = (y.max()+y.min())/2
    midz = (z.max()+z.min())/2
    ax.set_xlim3d(midx-mrang, midx+mrang)
    ax.set_ylim3d(midy-mrang, midy+mrang)
    ax.set_zlim3d(midz-mrang, midz+mrang)

  if plot_scatter:
    ## Plot disparity data as an image
    x,y,z = sqmesh.xyz[sqmesh.xyz[:,2]<sqmesh.xyz[:,2].max()].T

    ## Plot wireframe
    fig = figure(figsize=(10,8))
    ax = p3.Axes3D(fig, aspect='equal')
    title('Square mesh on 3D space', fontsize=20, fontweight='bold')

    ax.axis('equal')
    ax.scatter(x,y,z, c='b', marker='+')

    mrang = max([x.max()-x.min(), y.max()-y.min(), z.max()-z.min()])/2
    midx = (x.max()+x.min())/2
    midy = (y.max()+y.min())/2
    midz = (z.max()+z.min())/2
    ax.set_xlim3d(midx-mrang, midx+mrang)
    ax.set_ylim3d(midy-mrang, midy+mrang)
    ax.set_zlim3d(midz-mrang, midz+mrang)

  if plot_meshes:
    figure(figsize=(8,14))
    subplot(2,1,1)
    for p in sqmesh.con:
      #plot(sqmesh.xyz[p,0], sqmesh.xyz[p,1], 'g-')
      plot(q0[p,0], q0[p,1], 'b-')
    axis('equal')
    yla,ylb = ylim()
    ylim(ylb,yla)

    subplot(2,1,2)
    for p in sqmesh.con:
      plot(sqmesh.uv[p,0], sqmesh.uv[p,1], 'r-')

    axis('equal')
    yla,ylb = ylim()
    ylim(ylb,yla)

  if plot_cam:
    figure()
    imshow(cam_shot)
    for p in sqmesh.sqcon:
      plot(sqmesh.rs[p,0], sqmesh.rs[p,1], 'g-')
