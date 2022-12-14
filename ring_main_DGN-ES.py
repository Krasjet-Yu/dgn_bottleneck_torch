# Import all of the necessary pieces of Flow to run the experiments
from flow.core.params import SumoParams, EnvParams, NetParams, InitialConfig, \
    InFlows, SumoLaneChangeParams, SumoCarFollowingParams
from flow.core.params import VehicleParams
from flow.core.params import TrafficLightParams
import pandas as pd
from flow.controllers import SimLaneChangeController, ContinuousRouter
from flow.core.experiment import Experiment
from DGN_Env import para_produce_rl, Experiment
import logging

import datetime
import numpy as np
import time
import os
from DGN import DGN
from buffer import ReplayBuffer
from flow.core.params import SumoParams
### define some parameters
import pandas as pd
import os
import torch.optim as optim

import torch
import torch.nn as nn
import torch.optim as optim
import torch.autograd as autograd 
import torch.nn.functional as F

from ES_VSL import ES_VSL, SGD
import multiprocessing as mp


from config import *
def mkdir(path):
    folder = os.path.exists(path)
    if not folder:
        os.makedirs(path)
    else:
        print(path+'exist')


## define some environment parameters
exp_tag="dgn_ring"
build_adj=2
mkdir('{}_results'.format(exp_tag))
agent_num=6
neighbors=6
train_test=1 ##define train(1) or test(2)
num_runs=100
## build up settings
flow_params = para_produce_rl(NUM_AUTOMATED=agent_num) # NUM_AUTOMATED=agent_num
env = Experiment(flow_params=flow_params).env
rl_actions=None
convert_to_csv=True
model_path="./model/{0}_model.ckpt".format(exp_tag)
env.sim_params.emission_path='./{}_emission/'.format(exp_tag)
sim_params = SumoParams(sim_step=0.1, render=False, emission_path='./{0}_emission/'.format(exp_tag))
num_steps = env.env_params.horizon



n_ant = agent_num
observation_space = 3
n_actions = 6


buff = ReplayBuffer(capacity)
model = DGN(n_ant,observation_space,hidden_dim,n_actions)
model_tar = DGN(n_ant,observation_space,hidden_dim,n_actions)
model = model
model_tar = model_tar
optimizer = optim.Adam(model.parameters(), lr = 0.0001)

O = np.ones((batch_size,n_ant,observation_space))
Next_O = np.ones((batch_size,n_ant,observation_space))
Matrix = np.ones((batch_size,n_ant,n_ant))
Next_Matrix = np.ones((batch_size,n_ant,n_ant))


save_interal=20
rets = []
mean_rets = []
ret_lists = []
vels = []
mean_vels = []
std_vels = []
outflows = []
t = time.time()
times = []
vehicle_times = []
ploss=0
qloss=0
reg_loss=0
results=[]
scores=[]
losses=[]

# compension count
comp_cnt = 0
## save simulation videos
def render(render_mode='sumo_gui'):
    from flow.core.params import SimParams as sim_params
    sim_params.render=True
    save_render=True
    setattr(sim_params, 'num_clients', 1)
    # pick your rendering mode
    if render_mode == 'sumo_web3d':
        sim_params.num_clients = 2
        sim_params.render = False
    elif render_mode == 'drgb':
        sim_params.render = 'drgb'
        sim_params.pxpm = 4
    elif render_mode == 'sumo_gui':
        sim_params.render = False  # will be set to True below
    elif render_mode == 'no_render':
        sim_params.render = False
    if save_render:
        if render_mode != 'sumo_gui':
            sim_params.render = 'drgb'
            sim_params.pxpm = 4
        sim_params.save_render = True
def average(data):
    return sum(data)/len(data)

## todo how to define agent's relationship
if build_adj==1:
    # method 1:sort for the nearest speed vehicle
    def Adjacency( env ,neighbors=2):
        adj = []
        vels=np.array([env.k.vehicle.get_speed(veh_id) for veh_id in env.k.vehicle.get_rl_ids() ])
        orders = np.argsort(vels)
        for rl_id1 in env.k.vehicle.get_rl_ids():
            l = np.zeros([neighbors,len(env.k.vehicle.get_rl_ids())])
            j=0
            for k in range(neighbors):
                # modify this condition to define the adjacency matrix
                l[k,orders[k]]=1

            adj.append(l)
        return adj

if build_adj==2:
    # method2: sort for the nearest position vehicle
    def Adjacency(env ,neighbors=2):
        adj = []
        x_pos = np.array([env.k.vehicle.get_x_by_id(veh_id) for veh_id in env.k.vehicle.get_rl_ids()])
        exist_agent_num = len(x_pos)
            
        while len(x_pos) < agent_num:               # rl vehs reach the end, we should maintain the dim of array
            x_pos = np.append(x_pos, 0)

        headways = np.zeros([len(x_pos), len(x_pos)])
        for d in range(len(x_pos)):
            headways[d,:] = abs(x_pos-x_pos[d])
        
        orders = np.argsort(headways)

        for _ in range(len(x_pos)):
            l = np.zeros([neighbors,len(x_pos)])
            for k in range(neighbors):   # original range(neighbours)
                # modify this condition to define the adjacency matrix
                l[k,orders[k]]=1

            adj.append(l)
        return adj

if build_adj==3:
    ## method 3: consider both speed and position
    def Adjacency(env ,neighbors=2):
        des_vel=5
        adj = []
        x_pos = np.array([env.k.vehicle.get_x_by_id(veh_id) for veh_id in env.k.vehicle.get_rl_ids() ])
        x_vel = np.array([env.k.vehicle.get_speed(veh_id) for veh_id in env.k.vehicle.get_rl_ids() ])
        headways = np.zeros([len(env.k.vehicle.get_rl_ids()),len(env.k.vehicle.get_rl_ids())])
        for d in range(len(env.k.vehicle.get_rl_ids())):
            headways[d,:] = abs(x_pos-x_pos[d])+x_vel/(des_vel*abs(x_vel-x_vel[d])+0.01)

        orders = np.argsort(headways)
        for rl_id1 in env.k.vehicle.get_rl_ids():
            l = np.zeros([neighbors,len(env.k.vehicle.get_rl_ids())])
            j=0
            for k in range(neighbors):
                # modify this condition to define the adjacency matrix
                l[k,orders[k]]=1

            adj.append(l)
        return adj


def sign(k_id): return -1. if k_id % 2 == 0 else 1.  # mirrored sampling

def calculate_car_flow(env):
    # calculate the car flow
    vel = env.k.vehicle.get_rl_ids()[0]
    startPos = env.k.vehicle.get_x_by_id(vel)
    startTime = time.time()
    while True:
        endPos = env.k.vehicle.get_x_by_id(vel)
        if endPos == startPos: break
    endTime = time.time()

    car_flow = len(env.k.vehicle.get_ids()) / (endTime - startTime) # what is the coefficient ? Need I do the normalization ?
    
    return car_flow

def calculate_aver_speed(env):
    # calculate the car flow
    aver_speed = 0
    for veh_id in env.k.vehicle.get_ids():
        aver_speed += env.k.vehicle.get_speed(veh_id)
    
    aver_speed /= len(env.k.vehicle.get_ids())
    print("aver_speed : ",aver_speed)
    return aver_speed

def params_reshape(shapes, params):     # reshape to be a matrix
        p, start = [], 0
        for i, shape in enumerate(shapes):  # flat params to matrix
            n_w, n_b = shape[0] * shape[1], shape[1]
            p = p + [params[start: start + n_w].reshape(shape),
                    params[start + n_w: start + n_w + n_b].reshape((1, shape[1]))]
            start += n_w + n_b
        return p

# Evolution Strategy Vehicle Speed Limit
N_KID = 2
LR = .05                    # learning rate
SIGMA = .05                 # mutation strength or step size
N_CORE = mp.cpu_count() - 1
ES_TOTAL_SPL = []
ES_TOTAL_SCORES = []
REFRESH_PERIOD = 10
#SPEED_LIMITS = np.array([3, 4, 5, 6, 7, 8])
SPEED_LIMITS = np.array([5, 10, 12, 15, 17, 20])
# utility instead reward for update parameters (rank transformation)
base = N_KID * 2    # *2 for mirrored sampling
rank = np.arange(1, base + 1)
util_ = np.maximum(0, np.log(base / 2 + 1) - np.log(rank))
utility = util_ / util_.sum() - 1 / base

ESvsl = ES_VSL(observation_space, len(SPEED_LIMITS), N_KID, LR, SIGMA)
net_shapes, net_params = ESvsl.build_net()
VSL_optimizer = SGD(net_params, learning_rate=0.05)
pool = mp.Pool(processes=N_CORE)
mar = None

for i_episode in range(num_runs):
    # logging.info("Iter #" + str(i))
    print('episode is:',i_episode)
    ret = 0
    ret_list = []
    aset = [0] * agent_num
    aset_arg = [0] * agent_num
    obs = env.reset()
    #print("obs: ", obs)
    vec = np.zeros((1, neighbors))
    vec[0][0] = 1
    score=0 

    ES_rewards=[]   # save the reward of VSL network

    # Evolution Strategy
    t0 = time.time()
    noise_seed = np.random.randint(0, 2 ** 32 - 1, size=N_KID, dtype=np.uint32).repeat(2)    # mirrored sampling
    for k_id in range(N_KID*2):
        if i_episode % REFRESH_PERIOD != 0 and k_id != N_KID*2 -1:  # refresh the speed limit every 10 episode
            continue                                    # but we still need to run DQN in the last loop (N_KID*2-1)
        print("k_id is: ", k_id)
        params = net_params
        seed = noise_seed[k_id]
        np.random.seed(seed)
        params += sign(k_id) * SIGMA * np.random.randn(params.size)
        p = params_reshape(net_shapes, net_params)  # convert the flatten to matrix
        
        
        veh_state = np.array(list(obs.values())).reshape(agent_num,-1)
        print("veh_state : ", veh_state.shape)
        speed_limit = SPEED_LIMITS[ESvsl.get_action(p, veh_state)]
        ES_TOTAL_SPL.append(speed_limit)
        print("speed_limit get action : ", speed_limit)
        arrive = 0
        max_outflow = 0
        for j in range(num_steps):
            # manager actions
            # convert state into values
            state_ = np.array(list(obs.values())).reshape(agent_num,-1).tolist()

            adj = Adjacency(env ,neighbors=neighbors)

            state_= torch.tensor(np.asarray([state_]),dtype=torch.float) 

            adj_= torch.tensor(np.asarray(adj),dtype=torch.float)
            q = model(state_, adj_)[0]
            for i in range(n_ant):
                # if np.random.rand() > epsilon:
                #     a = 3*np.random.randn(n_actions)
                # else:
                a = q[i].argmax().item()
                aset_arg[i] = a
                #print('qi',q[i])
                action_lists = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3]
                #action_lists = [1, 2, 3, 4, 5, 6] 
                a = action_lists[a]
                #print("a : ", a)
                aset[i] = a

            action_dict = {}
            k=0
            for key, value in obs.items():       
                action_dict[key]=aset[k]         
                k+=1

            if i_episode % REFRESH_PERIOD == 0:             # refresh the speed_limit every 10 episode
                speed_limit_ = speed_limit

            next_state, reward, done, _ = env.step(action_dict, speed_limit_)

            next_adj = Adjacency(env ,neighbors=neighbors)


            while len(next_state) < agent_num:              # padding the matrix to maintain dimension
                next_state['comp_veh_{}'.format(comp_cnt)] = np.array([0,0,0])
                comp_cnt += 1


            next_state_ = np.array(list(next_state.values())).reshape(agent_num,-1).tolist()
            done_=np.array(list(done.values())).reshape(1,-1).tolist()


            reward_ = np.array(list(reward.values())).reshape(1,-1).tolist()
            # print('reward',np.average(reward_))
            buff.add(np.array(state_),aset_arg,np.average(reward_),np.array(next_state_),np.array(adj[-1]),np.array(next_adj[-1]), done_)
            obs = next_state
            # print('reward',reward)
            
            #print("done_", done_)
            score += sum(list(reward.values()))
            for i in range(len(done_)):
                if done_[0][-1] == True:
                    done_=1
                    break
            
            
            # if(j % 10 == 0):
            #     for veh_id in env.k.vehicle.get_rl_ids():
                    # print("position : ", env.k.vehicle.get_position(veh_id))
                    # print("veh_id : {}  edge : {}".format(veh_id, env.k.vehicle.get_edge(veh_id)))
                    # print("lane : ", env.k.vehicle.get_lane(veh_id))
                    # print("route : ", env.k.vehicle.get_route(veh_id))
                    # print("length : ", env.k.vehicle.get_length(veh_id))
        # calculate the car flow
            outflow = env.k.vehicle.get_outflow_rate(500)
            arrive += len(env.k.vehicle.get_arrived_ids())
            if j % 100 == 0:
                print("j : ", j)
                print("outflow : ", outflow)
                print("len of arrive id : ", arrive)
                print("max_outflow", max_outflow)
            
            max_outflow = max(max_outflow, outflow)
            if len(env.k.vehicle.get_rl_ids()) == 0:
                obs = env.reset()
                break;
        # aver_speed = calculate_aver_speed(env)
        ES_rewards.append((max_outflow - 500) / 100)        # set threshold of 500 outflow


    ES_rewards = np.array(ES_rewards)
    if i_episode % REFRESH_PERIOD == 0:                                   # train the VSL network every 10 episode

        kids_rank = np.argsort(ES_rewards)[::-1]               # rank kid id by reward

        
        cumulative_update = np.zeros_like(net_params)       # initialize update values
        for ui, k_id in enumerate(kids_rank):
            np.random.seed(noise_seed[k_id])                # reconstruct noise using seed
            cumulative_update += utility[ui] * sign(k_id) * np.random.randn(net_params.size)

        gradients = VSL_optimizer.get_gradients(cumulative_update/(2*N_KID*SIGMA))

        net_params += gradients
        kid_rewards = ES_rewards
        print(
            'Gen: ', i_episode,
            #'| Net_R: %.1f' % mar,
            '| Kid_avg_R: %.1f' % kid_rewards.mean(),
            '| Gen_T: %.2f' % (time.time() - t0),)

    ES_TOTAL_SCORES.append(ES_rewards.mean())

    scores.append(score/num_steps)


    np.save('scores.npy',scores)
    np.save('ES_spped_limit.npy', ES_TOTAL_SPL)
    np.save('ES_Total_scores.npy', ES_TOTAL_SCORES)

         ## calculate individual reward
        # for k in range(len(rewards)):

    if i_episode%save_interal==0:
            print(score/2000)
            score = 0
            torch.save(model.state_dict(), f'model_{i_episode}')


    if i_episode < 5:
        # print("episode is %d " % i_episode, "num_experience is %d\n" % buff.num_experiences)
        continue

    


    for e in range(n_epoch):
        batch = buff.getBatch(batch_size)
        for j in range(batch_size):
            sample = batch[j]
            O[j] = sample[0]
            Next_O[j] = sample[3]
            Matrix[j] = sample[4]
            Next_Matrix[j] = sample[5]

        q_values = model(torch.Tensor(O), torch.Tensor(Matrix))
        target_q_values = model_tar(torch.Tensor(Next_O), torch.Tensor(Next_Matrix)).max(dim = 2)[0]
        target_q_values = np.array(target_q_values.data)
        expected_q = np.array(q_values.data)
        
        for j in range(batch_size):
            sample = batch[j]
            for i in range(n_ant-1):
                #print('debug',np.average(sample[2][i][0]) + (1-sample[6])*GAMMA*target_q_values[j][i])
                # print('sample[2]',sample[2])
                # print('left whole ', expected_q.shape)
                # print("j {} i {} sampe[1][j] {}".format(j, i, sample[1][i]))
                # print('left',expected_q[j][i][sample[1][i]])
                # print('sample[6]', sample[6])
                # print('target_q_values[j][i] ', target_q_values[j][i])
                expected_q[j][i][sample[1][i]] = sample[2] + (1- (True in sample[6][0]))*GAMMA*target_q_values[j][i] ## dimension problem 
        
        loss = (q_values - torch.Tensor(expected_q)).pow(2).mean()
        losses.append(loss.detach().numpy())
        # print(losses)
        np.save('loss.npy',losses)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()


    if i_episode%5 == 0:
        model_tar.load_state_dict(model.state_dict())
    


env.terminate()

