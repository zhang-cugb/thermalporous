# load data for SPE10. must be run in serial

from firedrake import *
import numpy as np

Nx = 40
Ny = 40
y_shift = 60
x_shift = 15

def create_SPE10_slice2D(Nx, Ny, x_shift = 0, y_shift = 0, z_shift = 0, perm_factor = 1.0):
    import os
    dirname = os.path.dirname(__file__)
    
    Dx = 6.096
    Dy = 3.048
        
    kk = z_shift #layer

    Length_x = Nx*Dx
    Length_y = Ny*Dy

    lines = np.loadtxt(dirname + "/spe_phi.dat")
     
    single_line = np.reshape(lines, 60*220*85)

    field = np.zeros((Nx,Ny))
    for j in range(0,Ny):
        for i in range(0,Nx):
            field[i][j] = single_line[(i+x_shift)+(j+y_shift)*60+kk*220*60]

                
    phi_array = np.array(field)

    np.save(dirname + '/slice_phi.npy', phi_array)

    # load data for permeability
    lines = np.loadtxt(dirname + "/spe_perm.dat")
    print("Using SPE10 permeability/porosity fields")
    print("Permeability factor is: ", perm_factor)
    lines =  lines*9.869233e-10*perm_factor #converting md to mm^2  

    single_line = np.reshape(lines, [3, 60*220*85]).transpose()

    field = np.zeros((Nx,Ny))
    for j in range(0,Ny):
        for i in range(0,Nx):
            field[i][j] = single_line[(i+x_shift)+(j+y_shift)*60+kk*220*60,0]
    
    permx_array = np.array(field)

    np.save(dirname + '/slice_perm_x.npy', permx_array)

    for j in range(0,Ny):
        for i in range(0,Nx):
            field[i][j] = single_line[(i+x_shift)+(j+y_shift)*60+kk*220*60,1]
    
    permy_array = np.array(field)

    np.save(dirname + '/slice_perm_y.npy', permy_array)

