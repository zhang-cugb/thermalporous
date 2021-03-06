import numpy as np

from firedrake import *
from thermalporous.thermalmodel import ThermalModel

from firedrake.utils import cached_property
class TwoPhase(ThermalModel):
    def __init__(self, geo, case, params, end = 1.0, maxdt = 0.005, save = False, n_save = 2, small_dt_start = True, checkpointing = {}, solver_parameters = None, filename = "results/results.txt", dt_init_fact = 2**(-10), vector = False, gravity2D = False):
        self.name = "Two-phase"
        self.geo = geo
        self.case = case
        self.params = params
        self.mesh = geo.mesh
        self.comm = self.mesh.comm
        self.V = geo.V
        self.vector = vector
        self.solver_parameters = solver_parameters
        self.init_solver_parameters() #could overwrite self.vector and solver_parameters
        if self.vector:
            self.W = VectorFunctionSpace(self.mesh, "DQ", degree = 0, dim = 2)*self.V
            self.i_S_o = 1
        else:
            self.W = self.V*self.V*self.V
            self.i_S_o = 2
        geo.W = self.W
        self.save = save
        self.n_save = n_save
        self.small_dt_start = small_dt_start
        self.scaled_eqns = True # Weights equations such that they are of similar scale
        self.pressure_eqn = True # Water equation -> pressure equation. Needed for Schur complement approach. 
        self.geo.gravity2D = gravity2D
        if self.geo.dim == 2:
            self.init_variational_form = self.init_variational_form_2D
        elif self.geo.dim == 3:
            self.init_variational_form = self.init_variational_form_3D

        try:
            self.case.prod_wells
        except AttributeError:
            self.case.prod_wells = list()
        try:
            self.case.inj_wells
        except AttributeError:
            self.case.inj_wells = list()  
        try:
            self.case.heaters
        except AttributeError:
            self.case.heaters = list()  
        try:
            self.bcs = case.init_bcs()
        except AttributeError:
            self.bcs = []
        
        
        ThermalModel.__init__(self, end = end, maxdt = maxdt, save = save, n_save = n_save, small_dt_start = small_dt_start, checkpointing = checkpointing, filename = filename, dt_init_fact = dt_init_fact)
        
    def init_IC_uniform(self):
        p_ref = self.params.p_ref
        T_prod = self.params.T_prod
        S_o = self.params.S_o
        ic = Function(self.W)
        ic.sub(0).assign(Constant(p_ref))
        ic.sub(1).assign(Constant(T_prod))
        ic.sub(2).assign(Constant(S_o))
        return ic

    def init_variational_form_2D(self):
        W = self.W
        V = self.V
        mesh = self.mesh          
        K_x = self.geo.K_x
        K_y = self.geo.K_y
        ko = self.params.ko
        kw = self.params.kw
        kr = self.params.kr
        phi = self.geo.phi
        c_v_o = self.params.c_v_o
        c_v_w = self.params.c_v_w
        rho_r = self.params.rho_r
        c_r = self.params.c_r
        T_inj = self.params.T_inj
        oil_mu = self.params.oil_mu
        oil_rho = self.params.oil_rho
        water_mu = self.params.water_mu
        water_rho = self.params.water_rho
        
        # Initiate functions
        self.u = Function(W)
        self.u_ = Function(W)
        
        if self.vector:
            (pT, S_o) = split(self.u)
            (p, T) = split(pT)
            (pT_, S_o_) = split(self.u_)
            (p_, T_) = split(pT_)
            qr, s = TestFunctions(W)
            q, r = split(qr)
        else:
            (p, T, S_o) = split(self.u)
            (p_, T_, S_o_) = split(self.u_)
            q, r, s = TestFunctions(W)
        
        if False:
        # Determine capillary pressure. We set p_o = p
            p_w = p - self.params.capillary_pressure_linear(S_o)
            p_w_ = p_ - self.params.capillary_pressure_linear(S_o_)
            rel_perm_o = self.params.rel_perm_o_B_C
            rel_perm_w = self.params.rel_perm_w_B_C
        else:
            p_w = p
            p_w_ = p_
            rel_perm_o = self.params.rel_perm_o
            rel_perm_w = self.params.rel_perm_w
        
        rho_o = oil_rho(p, T)
        rho_w = water_rho(p_w, T)
        mu_o = oil_mu(T)
        mu_w = water_mu(T)
        kr_o = rel_perm_o(S_o)
        kr_w = rel_perm_w(S_o)
        
        # Define facet quantities
        n = FacetNormal(mesh)

        # Define difference between cell centers
        x = SpatialCoordinate(V.mesh())
        x_func = interpolate(x[0], V)
        y_func = interpolate(x[1], V)
        Delta_h = sqrt(jump(x_func)**2 + jump(y_func)**2)

        # harmonic average for permeability and conductivity
        K_x_facet = conditional(gt(avg(K_x), 0.0), K_x('+')*K_x('-') / avg(K_x), 0.0) 
        K_y_facet = conditional(gt(avg(K_y), 0.0), K_y('+')*K_y('-') / avg(K_y), 0.0)
        
        kT = phi*(S_o*ko + (1-S_o)*kw) + (1-phi)*kr
        
        K_facet = (K_x_facet*(abs(n[0]('+'))+abs(n[0]('-')))/2 + K_y_facet*(abs(n[1]('+'))+abs(n[1]('-')))/2) #need form to be symmetric wrt to '+' and '-'
        
        kT_facet = conditional(gt(avg(kT), 0.0), kT('+')*kT('-') / avg(kT), 0.0)        
        
        # weights for pressure and oil equations. Only uses initial saturation
        if self.scaled_eqns:
            if self.pressure_eqn:
                p_weight = self.params.T_prod
            else:
                p_weight = self.params.T_prod*(c_v_w*(1-self.params.S_o) + c_v_o*self.params.S_o)
            o_weight = self.params.T_prod*(c_v_w*(1-self.params.S_o) + c_v_o*self.params.S_o)
        else:
            p_weight = 1.0
            o_weight = 1.0

        if self.geo.gravity2D:
            g = self.params.g
            flow_o = jump(p)/Delta_h - g*avg(rho_o)*(abs(n[1]('+'))+abs(n[1]('-')))/2
            flow_w = jump(p_w)/Delta_h - g*avg(rho_w)*(abs(n[1]('+'))+abs(n[1]('-')))/2
        else:
            flow_o = jump(p)/Delta_h
            flow_w = jump(p_w)/Delta_h

        ## Solve a coupled problem 
        # conservation of mass equation WATER - "pressure equation"
        a_accum_w = phi*(rho_w*(1.0 - S_o) - water_rho(p_w_,T_)*(1.0 - S_o_))/self.dt*q*dx
        a_flow_w = K_facet*conditional(gt(flow_w, 0.0), kr_w('+')*rho_w('+')/mu_w('+'), kr_w('-')*rho_w('-')/mu_w('-'))*jump(q)*flow_w*dS
        # conservation of mass equation OIL - "saturation equation"
        a_accum_o = phi*(rho_o*S_o - oil_rho(p_,T_)*S_o_)/self.dt*s*dx
        a_flow_o = K_facet*conditional(gt(flow_o, 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(s)*flow_o*dS
        
        ## WEIGHTED SUM - pressure equation
        if self.pressure_eqn:
            a_accum_w = c_v_w*a_accum_w + c_v_o*(phi*(rho_o*S_o - oil_rho(p_,T_)*S_o_)/self.dt*q*dx)
            a_flow_w = c_v_w*a_flow_w + c_v_o*(K_facet*conditional(gt(flow_o, 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(q)*flow_o*dS)
        
        # conservation of energy equation
        a_Eaccum = phi*c_v_w*(rho_w*(1.0 - S_o)*T - water_rho(p_w_,T_)*(1.0 - S_o_)*T_)/self.dt*r*dx + phi*c_v_o*(rho_o*S_o*T - oil_rho(p_,T_)*S_o_*T_)/self.dt*r*dx + (1-phi)*rho_r*c_r*(T - T_)/self.dt*r*dx 
        a_advec = K_facet*conditional(gt(flow_w, 0.0), T('+')*kr_w('+')*rho_w('+')/mu_w('+'), T('-')*kr_w('-')*rho_w('-')/mu_w('-'))*c_v_w*jump(r)*flow_w*dS + K_facet*conditional(gt(flow_o, 0.0), T('+')*kr_o('+')*rho_o('+')/mu_o('+'), T('-')*kr_o('-')*rho_o('-')/mu_o('-'))*c_v_o*jump(r)*flow_o*dS
        a_diff = kT_facet*jump(T)/Delta_h*jump(r)*dS

        a = p_weight*a_accum_w + p_weight*a_flow_w + o_weight*a_accum_o + o_weight*a_flow_o + a_Eaccum + a_diff + a_advec
        self.F = a 
        
        rhow_o = rho_o
        rhow_w =rho_w
        rhow = water_rho(p_w, T_inj)

        ## Source terms using global deltas
        if self.case.name.startswith("Sources"):
            # production wells
            [rate, water_rate, oil_rate] = self.case.flow_rate_twophase_prod(p, T, S_o = S_o)
            self.prod_rate = rate
            self.oil_rate = oil_rate
            self.water_rate = water_rate
            tmp_o = self.case.deltas_prod*oil_rate
            tmp_w = self.case.deltas_prod*water_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx + p_weight*c_v_o*rhow_o*tmp_o*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx
            self.F -= rhow_w*tmp_w*c_v_w*T*r*dx + rhow_o*tmp_o*c_v_o*T*r*dx
            # injection wells
            inj_rate = self.case.flow_rate_inj(p, T, phase = 'water')
            self.inj_rate = inj_rate
            tmp = self.case.deltas_inj*inj_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow*tmp*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow*tmp*q*dx 
            self.F -= rhow*tmp*c_v_w*T_inj*r*dx
            # heaters
            self.F -= self.case.deltas_heaters*self.params.U*(T_inj-T)*r*dx

        # source terms
        for well in self.case.prod_wells:
            [rate, water_rate, oil_rate] = self.case.flow_rate_twophase(p, T, well, S_o = S_o)
            well.update({'rate': rate})
            well.update({'water_rate': water_rate})
            well.update({'oil_rate': oil_rate})
            tmp_o =  well['delta']*oil_rate
            tmp_w = well['delta']*water_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx + p_weight*c_v_o*rhow_o*tmp_o*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx
            self.F -= rhow_w*tmp_w*c_v_w*T*r*dx + rhow_o*tmp_o*c_v_o*T*r*dx
        for well in self.case.inj_wells:
            rate = self.case.flow_rate(p, T, well, phase = 'water') # only inject water
            well.update({'rate': rate})
            tmp = well['delta']*rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow*tmp*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow*tmp*q*dx
            self.F -= rhow*tmp*c_v_w*T_inj*r*dx
        for heater in self.case.heaters:
            tmp = heater['delta']
            self.F -= tmp*self.params.U*(T_inj-T)*r*dx

    def init_variational_form_3D(self):
        W = self.W
        V = self.V
        mesh = self.mesh          
        K_x = self.geo.K_x
        K_y = self.geo.K_y
        K_z = self.geo.K_z
        ko = self.params.ko
        kw = self.params.kw
        kr = self.params.kr
        phi = self.geo.phi
        c_v_o = self.params.c_v_o
        c_v_w = self.params.c_v_w
        rho_r = self.params.rho_r
        c_r = self.params.c_r
        T_inj = self.params.T_inj
        oil_mu = self.params.oil_mu
        oil_rho = self.params.oil_rho
        water_mu = self.params.water_mu
        water_rho = self.params.water_rho
        rel_perm_o = self.params.rel_perm_o
        rel_perm_w = self.params.rel_perm_w
        g = self.params.g
        
        # Initiate functions
        self.u = Function(W)
        self.u_ = Function(W)

        if self.vector:
            (pT, S_o) = split(self.u)
            (p, T) = split(pT)
            (pT_, S_o_) = split(self.u_)
            (p_, T_) = split(pT_)
            qr, s = TestFunctions(W)
            q, r = split(qr)
        else:
            (p, T, S_o) = split(self.u)
            (p_, T_, S_o_) = split(self.u_)
            q, r, s = TestFunctions(W)
            
        if False:
        # Determine capillary pressure. We set p_o = p
            p_w = p - self.params.capillary_pressure_linear(S_o)
            p_w_ = p_ - self.params.capillary_pressure_linear(S_o_)
            rel_perm_o = self.params.rel_perm_o_B_C
            rel_perm_w = self.params.rel_perm_w_B_C
        else:
            p_w = p
            p_w_ = p_
            rel_perm_o = self.params.rel_perm_o
            rel_perm_w = self.params.rel_perm_w
        
        rho_o = oil_rho(p, T)
        rho_w = water_rho(p_w, T)
        mu_o = oil_mu(T)
        mu_w = water_mu(T)
        kr_o = rel_perm_o(S_o)
        kr_w = rel_perm_w(S_o)
        
        # Define facet quantities
        n = FacetNormal(mesh)

        # Define difference between cell centers
        x = SpatialCoordinate(V.mesh())
        x_func = interpolate(x[0], V)
        y_func = interpolate(x[1], V)
        z_func = interpolate(x[2], V)
        Delta_h = sqrt(jump(x_func)**2 + jump(y_func)**2 + jump(z_func)**2)

        # harmonic average for permeability and conductivity
        K_x_facet = conditional(gt(avg(K_x), 0.0), K_x('+')*K_x('-') / avg(K_x), 0.0) 
        K_y_facet = conditional(gt(avg(K_y), 0.0), K_y('+')*K_y('-') / avg(K_y), 0.0)
        K_z_facet = conditional(gt(avg(K_z), 0.0), K_z('+')*K_z('-') / avg(K_z), 0.0)
        
        kT = phi*(S_o*ko + (1-S_o)*kw) + (1-phi)*kr
        
        K_facet = (K_x_facet*(abs(n[0]('+'))+abs(n[0]('-')))/2 + K_y_facet*(abs(n[1]('+'))+abs(n[1]('-')))/2) #need form to be symmetric wrt to '+' and '-'
        
        kT_facet = conditional(gt(avg(kT), 0.0), kT('+')*kT('-') / avg(kT), 0.0)        
        
        z_flow_w = jump(p_w)/Delta_h - g*avg(rho_w)
        z_flow_o = jump(p)/Delta_h - g*avg(rho_o)

        # weights for pressure and oil equations. Only uses initial saturation
        if self.scaled_eqns:
            if self.pressure_eqn:
                p_weight = self.params.T_prod
            else:
                p_weight = self.params.T_prod*(c_v_w*(1-self.params.S_o) + c_v_o*self.params.S_o)
            o_weight = self.params.T_prod*(c_v_w*(1-self.params.S_o) + c_v_o*self.params.S_o)
        else:
            p_weight = 1.0
            o_weight = 1.0

        ## Solve a coupled problem 
        # conservation of mass equation WATER - "pressure equation"
        a_accum_w = phi*(rho_w*(1.0 - S_o) - water_rho(p_w_,T_)*(1.0 - S_o_))/self.dt*q*dx
        a_flow_w = K_facet*conditional(gt(jump(p_w), 0.0), kr_w('+')*rho_w('+')/mu_w('+'), kr_w('-')*rho_w('-')/mu_w('-'))*jump(q)*jump(p)/Delta_h*dS_v
        a_flow_w_z = K_z_facet*conditional(gt(z_flow_w, 0.0), kr_w('+')*rho_w('+')/mu_w('+'), kr_w('-')*rho_w('-')/mu_w('-'))*jump(q)*z_flow_w*dS_h
        # conservation of mass equation OIL - "saturation equation"
        a_accum_o = phi*(rho_o*S_o - oil_rho(p_,T_)*S_o_)/self.dt*s*dx
        a_flow_o = K_facet*conditional(gt(jump(p), 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(s)*jump(p)/Delta_h*dS_v
        a_flow_o_z = K_z_facet*conditional(gt(z_flow_o, 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(s)*z_flow_o*dS_h
        
        
        ## WEIGHTED SUM - pressure equation
        if self.pressure_eqn:
            a_accum_w = c_v_w*a_accum_w + c_v_o*(phi*(rho_o*S_o - oil_rho(p_,T_)*S_o_)/self.dt*q*dx)
            a_flow_w = c_v_w*a_flow_w + c_v_o*(K_facet*conditional(gt(jump(p), 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(q)*jump(p)/Delta_h*dS_v)
            a_flow_w_z = c_v_w*a_flow_w_z + c_v_o*K_z_facet*conditional(gt(z_flow_o, 0.0), kr_o('+')*rho_o('+')/mu_o('+'), kr_o('-')*rho_o('-')/mu_o('-'))*jump(q)*z_flow_o*dS_h
        
        # conservation of energy equation
        a_Eaccum = phi*c_v_w*(rho_w*(1.0 - S_o)*T - water_rho(p_w_,T_)*(1.0 - S_o_)*T_)/self.dt*r*dx + phi*c_v_o*(rho_o*S_o*T - oil_rho(p_,T_)*S_o_*T_)/self.dt*r*dx + (1-phi)*rho_r*c_r*(T - T_)/self.dt*r*dx 
        a_advec = K_facet*conditional(gt(jump(p_w), 0.0), T('+')*kr_w('+')*rho_w('+')/mu_w('+'), T('-')*kr_w('-')*rho_w('-')/mu_w('-'))*c_v_w*jump(r)*jump(p)/Delta_h*dS_v + K_facet*conditional(gt(jump(p), 0.0), T('+')*kr_o('+')*rho_o('+')/mu_o('+'), T('-')*kr_o('-')*rho_o('-')/mu_o('-'))*c_v_o*jump(r)*jump(p)/Delta_h*dS_v
        a_advec_z = K_z_facet*conditional(gt(z_flow_w, 0.0), T('+')*kr_w('+')*rho_w('+')/mu_w('+'), T('-')*kr_w('-')*rho_w('-')/mu_w('-'))*c_v_w*jump(r)*z_flow_w*dS_h + K_z_facet*conditional(gt(z_flow_o, 0.0), T('+')*kr_o('+')*rho_o('+')/mu_o('+'), T('-')*kr_o('-')*rho_o('-')/mu_o('-'))*c_v_o*jump(r)*z_flow_o*dS_h
        a_diff = kT_facet*jump(T)/Delta_h*jump(r)*(dS_v + dS_h)

        a = p_weight*a_accum_w + p_weight*a_flow_w + p_weight*a_flow_w_z + o_weight*a_accum_o + o_weight*a_flow_o +o_weight* a_flow_o_z + a_Eaccum + a_advec + a_advec_z + a_diff
        self.F = a 

        rhow_o = rho_o
        rhow_w = rho_w
        rhow = water_rho(p_w, T_inj)

        ## Source terms using global deltas
        if self.case.name.startswith("Sources"):
            # production wells
            [rate, water_rate, oil_rate] = self.case.flow_rate_twophase_prod(p, T, S_o = S_o)
            self.prod_rate = rate
            self.oil_rate = oil_rate
            self.water_rate = water_rate
            tmp_o = self.case.deltas_prod*oil_rate
            tmp_w = self.case.deltas_prod*water_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx + p_weight*c_v_o*rhow_o*tmp_o*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx
            self.F -= rhow_w*tmp_w*c_v_w*T*r*dx + rhow_o*tmp_o*c_v_o*T*r*dx
            # injection wells
            inj_rate = self.case.flow_rate_inj(p, T, phase = 'water')
            self.inj_rate = inj_rate
            tmp = self.case.deltas_inj*inj_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow*tmp*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow*tmp*q*dx 
            self.F -= rhow*tmp*c_v_w*T_inj*r*dx
            # heaters
            self.F -= self.case.deltas_heaters*self.params.U*(T_inj-T)*r*dx

        # source terms
        for well in self.case.prod_wells:
            [rate, water_rate, oil_rate] = self.case.flow_rate_twophase(p, T, well, S_o = S_o)
            well.update({'rate': rate})
            well.update({'water_rate': water_rate})
            well.update({'oil_rate': oil_rate})
            tmp_o =  well['delta']*oil_rate
            tmp_w = well['delta']*water_rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx + p_weight*c_v_o*rhow_o*tmp_o*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow_w*tmp_w*q*dx + o_weight*rhow_o*tmp_o*s*dx
            self.F -= rhow_w*tmp_w*c_v_w*T*r*dx + rhow_o*tmp_o*c_v_o*T*r*dx
        for well in self.case.inj_wells:
            rate = self.case.flow_rate(p, T, well, phase = 'water') # only inject water
            well.update({'rate': rate})
            tmp = well['delta']*rate
            if self.pressure_eqn:
                self.F -= p_weight*c_v_w*rhow*tmp*q*dx # WEIGHTED SUM
            else:
                self.F -= p_weight*rhow*tmp*q*dx
            self.F -= rhow*tmp*c_v_w*T_inj*r*dx
        for heater in self.case.heaters:
            tmp = heater['delta']
            self.F -= tmp*self.params.U*(T_inj-T)*r*dx

    def init_solver_parameters(self):
        snes_atol = 1e-8
        snes_rtol = 1e-8
        newton_krylov = {
                "snes_type": "newtonls",
                #"snes_linesearch_type": "l2",
                #"snes_linesearch_maxstep": 1,
                #"snes_rtol": snes_rtol,
                #"snes_atol": snes_atol,
                "snes_monitor": None,
                "snes_converged_reason": None, 
                "snes_max_it": 25,

                "ksp_type": "fgmres",
                "ksp_converged_reason": None, 
                #"ksp_view": None,
                "ksp_max_it": 200,

                "ksp_gmres_restart": 200,
                "ksp_rtol": 1e-8,
                }

        newton_fas_krylov = {
                "snes_type": "newtonls",
                "snes_linesearch_type": "l2",
                "snes_linesearch_maxstep": 1,
                #"snes_rtol": snes_rtol,
                #"snes_atol": snes_atol,
                "snes_monitor": None,
                "snes_converged_reason": None, 
                "snes_max_it": 25,
                "snes_view": None,
                "snes_npc_side": "right",
                "npc_snes_type": "fas",
                "npc_snes_fas_cycles": 1,
                "npc_snes_fas_type": "kaskade",
                "npc_snes_fas_galerkin": False,
                "npc_snes_fas_full_downsweep": False,
                "npc_snes_monitor": None,
                "npc_snes_max_it": 1,
                "npc_fas_coarse_snes_type": "newtonls",
                "npc_fas_coarse_snes_monitor": None,
                "npc_fas_coarse_snes_converged_reason": None,
                "npc_fas_coarse_snes_max_it": 100,
                "npc_fas_coarse_snes_atol": 1.0e-14,
                "npc_fas_coarse_snes_rtol": 1.0e-14,
                #"npc_fas_coarse_snes_linesearch_type": "l2",
                "npc_fas_coarse_ksp_type": "preonly",
                #"npc_fas_coarse_ksp_converged_reason": None,
                "npc_fas_coarse_ksp_max_it": 1,
                "npc_fas_coarse_pc_type": "lu",
                "npc_fas_coarse_pc_factor_mat_solver_type": "mumps",
                "npc_fas_levels_snes_monitor": None,

                "ksp_type": "fgmres",
                "ksp_converged_reason": None, 
                #"ksp_view": None,
                "ksp_max_it": 200,

                "ksp_gmres_restart": 200,
                "ksp_rtol": 1e-8,
                }

        

        v_cycle = {"ksp_type": "preonly",
                    "pc_type": "hypre",
                    "pc_hypre_type" : "boomeramg",
                    "pc_hypre_boomeramg_max_iter": 1,
                    }   
        
        mg_v_cycle = {"ksp_type": "preonly",
                      "pc_type": "mg",
                     }
        
        mg_pardecomp = {"ksp_type": "preonly",
                        "pc_type": "mg",
                        "pc_mg_cycles": 1,
                        "pc_mg_type": "kaskade",
                        "pc_mg_galerkin": False,
                        "pc_mg_full_downsweep": False,
                        #"pc_monitor": None,
                        #"pc_max_it": 1,
                        "mg_levels_pc_type": "python",
                        "mg_levels_pc_python_type": "firedrake.PatchPC",
                        "mg_levels_pc_max_it": 1,
                        "mg_levels_pc_convergence_test": "skip",
                        #"mg_levels_pc_converged_reason": None,
                        #"mg_levels_pc_monitor": None,
                        "mg_levels_patch_pc_patch_construct_type": "pardecomp",
                        "mg_levels_patch_pc_patch_pardecomp_overlap": 1,
                        "mg_levels_patch_pc_patch_partition_of_unity": True,
                        "mg_levels_patch_pc_patch_sub_mat_type": "seqaij",
                        "mg_levels_patch_pc_patch_local_type": "additive",
                        "mg_levels_patch_pc_patch_symmetrise_sweep": False,
                        "mg_levels_patch_sub_ksp_type": "preonly",
                        #"mg_levels_patch_sub_ksp_converged_reason": None,
                        "mg_levels_patch_sub_pc_type": "lu",
                        "mg_levels_patch_sub_pc_factor_mat_solver_type": "mumps",
                        "mg_coarse_ksp_type": "preonly",
                        #"mg_coarse_ksp_converged_reason": None,
                        "mg_coarse_ksp_max_it": 1,
                        "mg_coarse_pc_type": "lu",
                        "mg_coarse_pc_factor_mat_solver_type": "mumps",
                         }
        mg_v_cycle = mg_pardecomp
        
        #v_cycle = mg_v_cycle
        
        mg_python = {"ksp_type": "preonly",
                     "pc_type": "python",
                     "pc_python_type": "firedrake.AssembledPC",
                     }

        lu = {"ksp_type": "preonly",
              "pc_type": "lu",}


        pc_cptr = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "python,bjacobi",

                "sub_0_pc_python_type": "thermalporous.preconditioners.CPTRStage1PC",
                "sub_0_cpr_stage1_pc_type": "fieldsplit",
                "sub_0_cpr_stage1_pc_fieldsplit_type": "schur",
                "sub_0_cpr_stage1_pc_fieldsplit_schur_fact_type": "FULL",
                
                "sub_0_cpr_stage1_fieldsplit_1_ksp_type": "preonly",
                "sub_0_cpr_stage1_fieldsplit_1_pc_type": "python",
                "sub_0_cpr_stage1_fieldsplit_1_pc_python_type": "thermalporous.preconditioners.ConvDiffSchurTwoPhasesPC",
                "sub_0_cpr_stage1_fieldsplit_1_schur": v_cycle,

                "sub_0_cpr_stage1_fieldsplit_0": v_cycle,
                
                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }
        
        pc_cptramg = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "python,bjacobi",

                "sub_0_pc_python_type": "thermalporous.preconditioners.CPTRStage1PC",
                "sub_0_cpr_stage1_pc_type": "hypre", #v_cycle,
                "sub_0_cpr_stage1_pc_hypre_type" : "boomeramg",
                "sub_0_cpr_stage1_pc_hypre_boomeramg_max_iter": 1,
                
                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }
        pc_cptramg_QI = {**pc_cptramg, "sub_0_cpr_decoup": "QI"}
        pc_cptramg_TI = {**pc_cptramg, "sub_0_cpr_decoup": "TI"}
        
        pc_cptrlu = {"pc_type": "composite",
        "pc_composite_type": "multiplicative",
        "pc_composite_pcs": "python,bjacobi",

        "sub_0_pc_python_type": "thermalporous.preconditioners.CPTRStage1PC",
        "sub_0_cpr_stage1_pc_type": "lu",
        
        "sub_1_sub_pc_type": "ilu",
        "sub_1_sub_pc_factor_levels": 0,
        "mat_type": "aij",
                }
        pc_cptrlu_QI = {**pc_cptrlu, "sub_0_cpr_decoup": "QI"}
        pc_cptrlu_TI = {**pc_cptrlu, "sub_0_cpr_decoup": "TI"}

        pc_cpr = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "python,bjacobi",

                "sub_0_pc_python_type": "thermalporous.preconditioners.CPRStage1PC",
                "sub_0_cpr_stage1": v_cycle,

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }
        
        pc_cpr_QI = {**pc_cpr, "sub_0_cpr_decoup": "QI"}
        pc_cpr_TI = {**pc_cpr, "sub_0_cpr_decoup": "TI"}
        pc_cpr_QI_temp = {**pc_cpr, "sub_0_cpr_decoup": "QI_temp"}
        pc_cpr_TI_temp = {**pc_cpr, "sub_0_cpr_decoup": "TI_temp"}

        pc_cptr_a11 = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "python,bjacobi",

                "sub_0_pc_python_type": "thermalporous.preconditioners.CPTRStage1PC",
                "sub_0_cpr_stage1_pc_type": "fieldsplit",
                "sub_0_cpr_stage1_pc_fieldsplit_type": "schur",
                "sub_0_cpr_stage1_pc_fieldsplit_schur_fact_type": "FULL",
                
                "sub_0_cpr_stage1_pc_fieldsplit_schur_precondition": "a11",
                "sub_0_cpr_stage1_fieldsplit_1": v_cycle,

                "sub_0_cpr_stage1_fieldsplit_0": v_cycle,
                
                "sub_1_pc_bjacobi_blocks": 1,
                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }

        pc_cpr_gmres = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "fieldsplit,bjacobi",
                
                "sub_0_pc_fieldsplit_0_fields": "0",
                "sub_0_pc_fieldsplit_1_fields": "1,2",
                "sub_0_pc_fieldsplit_type": "additive",
                "sub_0_fieldsplit_0": v_cycle,    
                "sub_0_fieldsplit_1_ksp_type": "gmres",
                "sub_0_fieldsplit_1_ksp_max_it": 0,
                "sub_0_fieldsplit_1_pc_type": "none",

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }

        pc_cprmg_gmres = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "fieldsplit,bjacobi",
                
                "sub_0_pc_fieldsplit_0_fields": "0",
                "sub_0_pc_fieldsplit_1_fields": "1,2",
                "sub_0_pc_fieldsplit_type": "additive",
                "sub_0_fieldsplit_0": mg_v_cycle,    
                "sub_0_fieldsplit_1_ksp_type": "gmres",
                "sub_0_fieldsplit_1_ksp_max_it": 0,
                "sub_0_fieldsplit_1_pc_type": "none",

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }

        pc_cprilu1_gmres = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "fieldsplit,bjacobi",
                
                "sub_0_pc_fieldsplit_0_fields": "0",
                "sub_0_pc_fieldsplit_1_fields": "1,2",
                "sub_0_pc_fieldsplit_type": "additive",
                "sub_0_fieldsplit_0": v_cycle,    
                "sub_0_fieldsplit_1_ksp_type": "gmres",
                "sub_0_fieldsplit_1_ksp_max_it": 0,
                "sub_0_fieldsplit_1_pc_type": "none",

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 1,
                "mat_type": "aij",
                }

        pc_cptr_gmres = {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "fieldsplit,bjacobi",
                
                "sub_0_pc_fieldsplit_0_fields": "0,1",
                "sub_0_pc_fieldsplit_1_fields": "2",
                "sub_0_pc_fieldsplit_type": "additive",
                
                "sub_0_fieldsplit_0_pc_type": "fieldsplit", 
                "sub_0_fieldsplit_0_pc_fieldsplit_type": "schur",
                "sub_0_fieldsplit_0_pc_fieldsplit_schur_fact_type": "FULL",
        
                "sub_0_fieldsplit_0_fieldsplit_1_ksp_type": "preonly",
                "sub_0_fieldsplit_0_fieldsplit_1_pc_type": "python",
                "sub_0_fieldsplit_0_fieldsplit_1_pc_python_type": "thermalporous.preconditioners.ConvDiffSchurTwoPhasesPC",
                "sub_0_fieldsplit_0_fieldsplit_1_schur": v_cycle,
                
                "sub_0_fieldsplit_0_fieldsplit_0": v_cycle,       
                
                "sub_0_fieldsplit_1_ksp_type": "gmres",
                "sub_0_fieldsplit_1_ksp_max_it": 0,
                "sub_0_fieldsplit_1_pc_type": "none",

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }
        
        pc_cptramg_gmres = {"pc_type": "composite",
                            "pc_composite_type": "multiplicative",
                            "pc_composite_pcs": "fieldsplit,bjacobi",
                            
                            "sub_0_pc_fieldsplit_type": "additive",
                            
                            "sub_0_fieldsplit_0": v_cycle,    
                            
                            "sub_0_fieldsplit_1_ksp_type": "gmres",
                            "sub_0_fieldsplit_1_ksp_max_it": 0,
                            "sub_0_fieldsplit_1_pc_type": "none",

                            "sub_1_sub_pc_type": "ilu",
                            "sub_1_sub_pc_factor_levels": 0,
                            "mat_type": "aij",
                            }
        
        pc_cptrlu_gmres =  {"pc_type": "composite",
                "pc_composite_type": "multiplicative",
                "pc_composite_pcs": "fieldsplit,bjacobi",
                
                "sub_0_pc_fieldsplit_0_fields": "0,1",
                "sub_0_pc_fieldsplit_1_fields": "2",
                "sub_0_pc_fieldsplit_type": "additive",
                
                "sub_0_fieldsplit_0_pc_type": "lu",
                
                "sub_0_fieldsplit_1_ksp_type": "gmres",
                "sub_0_fieldsplit_1_ksp_max_it": 0,
                "sub_0_fieldsplit_1_pc_type": "none",

                "sub_1_sub_pc_type": "ilu",
                "sub_1_sub_pc_factor_levels": 0,
                "mat_type": "aij",
                }

        pc_ilu = {"pc_type": "ilu",
                  "pc_factor_levels": 0,
                  "mat_type": "aij",
                  }
        
        pc_hypre = {"pc_type": "hypre",
                  "pc_hypre_type": "boomeramg",
                  "mat_type": "aij",
                  }
        
        pc_mg = {"pc_type": "mg",
                 "mat_type": "aij",
                 "mg_coarse_ksp_type": "preonly",
                 "mg_coarse_pc_type": "python",
                 "mg_coarse_pc_python_type": "firedrake.AssembledPC",
                 "mg_coarse_assembed_pc_type": "lu",
                 "mg_coarse_assembled_mat_view": None,}

        pc_lu = {"ksp_type": "preonly",
                 "mat_type": "aij",
                 "pc_type": "lu",
                 }
        

        pc_bilu = {"pc_type": "bjacobi",
                   "sub_pc_type": "ilu",
                   "sub_pc_factor_levels": 1,
                   "mat_type": "aij",
                   }

        faspardecomp = {}
        ngmresfaspardecomp = {
               "mat_type": "matfree",
               "snes_type": "ngmres",
               "snes_monitor": None,
               "snes_max_it": 100,
               "snes_npc_side": "right",
               "snes_atol": snes_atol,
               "snes_rtol": snes_rtol,
               "snes_converged_reason": None,
               "npc_snes_type": "fas",
               "npc_snes_fas_cycles": 1,
               "npc_snes_fas_type": "kaskade",
               "npc_snes_fas_galerkin": False,
               "npc_snes_fas_full_downsweep": False,
               #"npc_snes_monitor": None,
               "npc_snes_max_it": 1,
               "npc_fas_levels_snes_type": "python",
               "npc_fas_levels_snes_python_type": "firedrake.PatchSNES",
               "npc_fas_levels_snes_max_it": 1,
               "npc_fas_levels_snes_convergence_test": "skip",
               #"npc_fas_levels_snes_converged_reason": None,
               #"npc_fas_levels_snes_monitor": None,
               "npc_fas_levels_snes_linesearch_type": "basic",
               "npc_fas_levels_snes_linesearch_damping": 1.0,
               "npc_fas_levels_patch_snes_patch_construct_type": "pardecomp",
               "npc_fas_levels_patch_snes_patch_pardecomp_overlap": 1,
               "npc_fas_levels_patch_snes_patch_partition_of_unity": True,
               "npc_fas_levels_patch_snes_patch_sub_mat_type": "seqaij",
               "npc_fas_levels_patch_snes_patch_local_type": "additive",
               "npc_fas_levels_patch_snes_patch_symmetrise_sweep": False,
               "npc_fas_levels_patch_sub_snes_type": "newtonls",
               #"npc_fas_levels_patch_sub_snes_monitor": None,
               "npc_fas_levels_patch_sub_snes_atol": 1.0e-11,
               "npc_fas_levels_patch_sub_snes_rtol": 1.0e-11,
               #"npc_fas_levels_patch_sub_snes_converged_reason": None,
               "npc_fas_levels_patch_sub_snes_linesearch_type": "basic",
               "npc_fas_levels_patch_sub_ksp_type": "preonly",
               "npc_fas_levels_patch_sub_pc_type": "lu",
               "npc_fas_levels_patch_sub_pc_factor_mat_solver_type": "umfpack",
               "npc_fas_coarse_snes_type": "newtonls",
               #"npc_fas_coarse_snes_monitor": None,
               #"npc_fas_coarse_snes_converged_reason": None,
               "npc_fas_coarse_snes_max_it": 100,
               "npc_fas_coarse_snes_atol": 1.0e-14,
               "npc_fas_coarse_snes_rtol": 1.0e-14,
               "npc_fas_coarse_snes_linesearch_type": "l2",
               "npc_fas_coarse_ksp_type": "preonly",
               "npc_fas_coarse_ksp_max_it": 1,
               "npc_fas_coarse_pc_type": "python",
               "npc_fas_coarse_pc_python_type": "firedrake.AssembledPC",
               "npc_fas_coarse_assembled_mat_type": "aij",
               "npc_fas_coarse_assembled_pc_type": "lu",
               "npc_fas_coarse_assembled_pc_factor_mat_solver_type": "mumps",
                }
        
        newtonaijfaspardecomp  = {
                "mat_type": "aij",
                "snes_type": "newtonls",
                "snes_monitor": None,
                "snes_linesearch_type": "l2",
                #"snes_linesearch_monitor": None,
                "snes_linesearch_maxstep": 1,
                "snes_view": None,
                "ksp_type": "preonly",
                #"ksp_pc_side": "right",
                "pc_type": "lu",
                #"pc_mg_type" : "full",
                "ksp_monitor": None,
                "snes_max_it": 100,
                "snes_npc_side": "right",
                "snes_atol": snes_atol,
                "snes_rtol": snes_rtol,
                "snes_converged_reason": None,
                "npc_snes_type": "fas",
                "npc_snes_fas_cycles": 1,
                "npc_snes_fas_type": "kaskade",
                "npc_snes_fas_galerkin": False,
                "npc_snes_fas_full_downsweep": False,
                "npc_snes_monitor": None,
                "npc_snes_max_it": 1,
                "npc_fas_levels_snes_type": "python",
                "npc_fas_levels_snes_python_type": "firedrake.PatchSNES",
                "npc_fas_levels_snes_max_it": 1,
                "npc_fas_levels_snes_convergence_test": "skip",
                "npc_fas_levels_snes_converged_reason": None,
                "npc_fas_levels_snes_monitor": None,
                "npc_fas_levels_snes_linesearch_type": "basic",
                "npc_fas_levels_snes_linesearch_damping": 1.0,
                "npc_fas_levels_patch_snes_patch_construct_type": "pardecomp",
                "npc_fas_levels_patch_snes_patch_pardecomp_overlap": 1,
                "npc_fas_levels_patch_snes_patch_partition_of_unity": True,
                "npc_fas_levels_patch_snes_patch_sub_mat_type": "seqaij",
                "npc_fas_levels_patch_snes_patch_local_type": "additive",
                "npc_fas_levels_patch_snes_patch_symmetrise_sweep": False,
                "npc_fas_levels_patch_sub_snes_type": "newtonls",
                "npc_fas_levels_patch_sub_snes_monitor": None,
                "npc_fas_levels_patch_sub_snes_atol": 1.0e-10,
                "npc_fas_levels_patch_sub_snes_rtol": 1.0e-10,
                "npc_fas_levels_patch_sub_snes_stol": 0.0,
                "npc_fas_levels_patch_sub_snes_converged_reason": None,
                "npc_fas_levels_patch_sub_snes_linesearch_type": "basic",
                "npc_fas_levels_patch_sub_ksp_type": "preonly",
                "npc_fas_levels_patch_sub_pc_type": "lu",
                "npc_fas_levels_patch_sub_pc_factor_mat_solver_type": "mumps",
                "npc_fas_coarse_snes_type": "newtonls",
                "npc_fas_coarse_snes_monitor": None,
                "npc_fas_coarse_snes_converged_reason": None,
                "npc_fas_coarse_snes_max_it": 100,
                "npc_fas_coarse_snes_atol": 1.0e-14,
                "npc_fas_coarse_snes_rtol": 1.0e-14,
                "npc_fas_coarse_snes_linesearch_type": "l2",
                "npc_fas_coarse_ksp_type": "preonly",
                "npc_fas_coarse_ksp_converged_reason": None,
                "npc_fas_coarse_ksp_max_it": 1,
                "npc_fas_coarse_pc_type": "lu",
                "npc_fas_coarse_pc_factor_mat_solver_type": "mumps",
                }
        newtonmgpardecomp =  {
                 "mat_type": "aij",
                 "snes_type": "newtonls",
                 "snes_linesearch_type": "l2",
                 "snes_max_it": 100,
                 #"snes_linesearch_monitor": None,
                 "snes_linesearch_maxstep": 1,
                 #"snes_monitor": None,
                 "snes_atol": snes_atol,
                 "snes_rtol": snes_rtol,
                 "snes_converged_reason": None, 
                 "ksp_type":"fgmres", 
                 "ksp_max_it": 40,
                 #"ksp_monitor": None,
                 "ksp_converged_reason": None,
                 "pc_type": "mg",
                 "pc_mg_cycles": 1,
                 "pc_mg_type": "kaskade",
                 "pc_mg_galerkin": False,
                 "pc_mg_full_downsweep": False,
                 #"pc_monitor": None,
                 #"pc_max_it": 1,
                 "mg_levels_pc_type": "python",
                 "mg_levels_pc_python_type": "firedrake.PatchPC",
                 "mg_levels_pc_max_it": 1,
                 "mg_levels_pc_convergence_test": "skip",
                 #"mg_levels_pc_converged_reason": None,
                 #"mg_levels_pc_monitor": None,
                 "mg_levels_patch_pc_patch_construct_type": "pardecomp",
                 "mg_levels_patch_pc_patch_pardecomp_overlap": 1,
                 "mg_levels_patch_pc_patch_partition_of_unity": True,
                 "mg_levels_patch_pc_patch_sub_mat_type": "seqaij",
                 "mg_levels_patch_pc_patch_local_type": "additive",
                 "mg_levels_patch_pc_patch_symmetrise_sweep": False,
                 "mg_levels_patch_sub_ksp_type": "preonly",
                 #"mg_levels_patch_sub_ksp_converged_reason": None,
                 "mg_levels_patch_sub_pc_type": "lu",
                 "mg_levels_patch_sub_pc_factor_mat_solver_type": "mumps",
                 "mg_coarse_ksp_type": "preonly",
                 #"mg_coarse_ksp_converged_reason": None,
                 "mg_coarse_ksp_max_it": 1,
                 "mg_coarse_pc_type": "lu",
                 "mg_coarse_pc_factor_mat_solver_type": "mumps",
                }
        #parameters = newton_krylov
        parameters = newton_fas_krylov

        if self.solver_parameters is None:
            self.solver_parameters = "pc_cptr_gmres"
        
        if isinstance(self.solver_parameters, str):
            if self.solver_parameters == "pc_cptr":
                parameters.update(pc_cptr)
            if self.solver_parameters == "pc_cptramg":
                parameters.update(pc_cptramg)
                self.vector = True
            if self.solver_parameters == "pc_cptramg_QI":
                parameters.update(pc_cptramg_QI)
                self.vector = True
            if self.solver_parameters == "pc_cptramg_TI":
                parameters.update(pc_cptramg_TI)
                self.vector = True
            if self.solver_parameters == "pc_cptrlu":
                parameters.update(pc_cptrlu)
                self.vector = True
            if self.solver_parameters == "pc_cptrlu_QI":
                parameters.update(pc_cptrlu_QI)
                self.vector = True
            if self.solver_parameters == "pc_cptrlu_TI":
                parameters.update(pc_cptrlu_TI)
                self.vector = True                
            if self.solver_parameters == "pc_cptramg_gmres":
                parameters.update(pc_cptramg_gmres)
                self.vector = True
            elif self.solver_parameters == "pc_cptr_a11":
                parameters.update(pc_cptr_a11)    
            elif self.solver_parameters == "pc_cptr_gmres":
                parameters.update(pc_cptr_gmres)
            elif self.solver_parameters == "pc_cptrlu_gmres":
                parameters.update(pc_cptrlu_gmres)
            elif self.solver_parameters == "pc_cpr_gmres":
                parameters.update(pc_cpr_gmres)
            elif self.solver_parameters == "pc_cprmg_gmres":
                parameters.update(pc_cprmg_gmres)
            elif self.solver_parameters == "pc_cpr":
                parameters.update(pc_cpr)
            elif self.solver_parameters == "pc_cpr_QI":
                parameters.update(pc_cpr_QI)
            elif self.solver_parameters == "pc_cpr_TI":
                parameters.update(pc_cpr_TI)
            elif self.solver_parameters == "pc_cpr_QI_temp":
                parameters.update(pc_cpr_QI_temp)
            elif self.solver_parameters == "pc_cpr_TI_temp":
                parameters.update(pc_cpr_TI_temp)
            elif self.solver_parameters == "pc_cprilu1_gmres":
                parameters.update(pc_cprilu1_gmres)                
            elif self.solver_parameters == "pc_ilu":
                parameters.update(pc_ilu)
            elif self.solver_parameters == "pc_hypre":    
                parameters.update(pc_hypre)
            elif self.solver_parameters == "pc_lu":    
                parameters.update(pc_lu)
            elif self.solver_parameters == "pc_bilu":    
                parameters.update(pc_bilu)
            elif self.solver_parameters == "pc_mg":    
                parameters.update(pc_mg)
            elif self.solver_parameters == "faspardecomp":
                parameters = faspardecomp
            elif self.solver_parameters == "ngmresfaspardecomp":
                parameters = ngmresfaspardecomp
            elif self.solver_parameters == "newtonaijfaspardecomp":
                parameters = newtonaijfaspardecomp
            elif self.solver_parameters == "newtonmgpardecomp":
                parameters = newtonmgpardecomp

            self.solver_parameters = parameters
            
        if "sub_0_cpr_decoup" in self.solver_parameters:
            self.decoup = self.solver_parameters["sub_0_cpr_decoup"]
        else:
            self.decoup = "No"

 
    @cached_property
    def appctx(self):
        return {"pressure_space": 0, "temperature_space": 1, "saturation_space": 2, "params": self.params, "geo": self.geo, "dt": self.dt, "case": self.case, "u_": self.u_, "decoup": self.decoup, "vector": self.vector}
