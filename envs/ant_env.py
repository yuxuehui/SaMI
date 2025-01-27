import os
import numpy as np
# import tensorflow as tf
from gym import utils
from gym.envs.mujoco import mujoco_env
import gymnasium.spaces as spaces
from .utils import convert_observation_to_space

class AntEnv(mujoco_env.MujocoEnv, utils.EzPickle):
    def __init__(self, mass_scale_set=[0.85, 0.9, 0.95, 1.0], damping_scale_set=[1.0], causal_dim=-1, causal_hidden_dim=-1):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        self.causal_dim = causal_dim
        self.causal_hidden_dim = causal_hidden_dim
        self.current_trajectory_length = 0
        self.current_trajectory_reward = 0
        self.max_eps_length = 2000
        mujoco_env.MujocoEnv.__init__(self, '%s/assets/ant.xml' % dir_path, 5)

        self.original_mass = np.copy(self.model.body_mass)
        self.original_damping = np.copy(self.model.dof_damping)

        self.mass_scale_set = mass_scale_set
        self.damping_scale_set = damping_scale_set

        utils.EzPickle.__init__(self, mass_scale_set, damping_scale_set)

        ob = self._get_obs()
        self.observation_space = convert_observation_to_space(ob)
        bounds = self.model.actuator_ctrlrange.copy()
        low, high = bounds.T
        self.action_space = spaces.Box(low=low, high=high, dtype=np.float32)


    def _set_observation_space(self, observation):
        super(AntEnv, self)._set_observation_space(observation)
        proc_observation = self.obs_preproc(observation['observation'][None])
        self.proc_observation_space_dims = proc_observation.shape[-1]

    def step(self, a):
        self.xposbefore = self.get_body_com("torso")[0]
        self.do_simulation(a, self.frame_skip)
        xposafter = self.get_body_com("torso")[0]

        # reward_ctrl = -0.005 * np.square(a).sum()
        reward_ctrl = -0.01 * np.square(a).sum()
        reward_run = (xposafter - self.xposbefore) / self.dt
        # reward_contact = 0.0
        reward_contact = (
            -0.5  * np.sum(np.square(np.clip(self.sim.data.cfrc_ext, -1, 1)))
        )
        # reward_survive = 0.05
        reward_survive = 1.0
        reward = reward_run + reward_ctrl + reward_contact + reward_survive
        self.current_trajectory_length += 1
        self.current_trajectory_reward += reward
        done = False
        ob = self._get_obs()
        # print(self.current_trajectory_length)
        if self.current_trajectory_length == self.max_eps_length-1:
            return ob, reward, True, dict(
                is_success=False,
                episode=dict(
                    r = self.current_trajectory_reward,
                    l = self.current_trajectory_length
                ),
                reward_forward=reward_run,
                reward_ctrl=reward_ctrl,
                reward_contact=reward_contact,
                reward_survive=reward_survive)
        else:
            return ob, reward, False, dict(
                is_success=False,
                episode=dict(
                    r = self.current_trajectory_reward,
                    l = self.current_trajectory_length
                ),
                reward_forward=reward_run,
                reward_ctrl=reward_ctrl,
                reward_contact=reward_contact,
                reward_survive=reward_survive)

    def _get_obs(self):
        
        obs = {'observation': np.concatenate([
            ((self.get_body_com("torso")[0] - self.xposbefore) / self.dt).flat,
            self.sim.data.qpos.flat[2:],
            self.sim.data.qvel.flat,
        ]).astype(np.float32)}
        if self.causal_dim > 0:
            obs['causal'] = np.random.randn(self.causal_dim).astype(np.float32)
            obs['hidden_h'] = np.zeros((self.causal_hidden_dim,),dtype=np.float32) # 这个时刻rnn的输出
            obs['hidden_c'] = np.zeros((self.causal_hidden_dim,),dtype=np.float32) # 这个时刻rnn的输出
        return obs

    def obs_preproc(self, obs):
        return obs[..., 1:]

    def obs_postproc(self, obs, pred):
        if isinstance(obs, np.ndarray):
            return np.concatenate([pred[..., :1], obs[..., 1:] + pred[..., 1:]], axis=-1)
        else:
            return tf.concat([pred[..., :1], obs[..., 1:] + pred[..., 1:]], axis=-1)

    def targ_proc(self, obs, next_obs):
        return np.concatenate([next_obs[..., :1], next_obs[..., 1:] - obs[..., 1:]], axis=-1)

    def reset_model(self):
        qpos = self.init_qpos + self.np_random.uniform(size=self.model.nq, low=-.1, high=.1)
        qvel = self.init_qvel + self.np_random.randn(self.model.nv) * .1
        self.set_state(qpos, qvel)
        self.xposbefore = self.get_body_com("torso")[0]

        random_index = self.np_random.randint(len(self.mass_scale_set))
        self.mass_scale = self.mass_scale_set[random_index]

        random_index = self.np_random.randint(len(self.damping_scale_set))
        self.damping_scale = self.damping_scale_set[random_index]

        self.change_env()
        return self._get_obs()

    def reward(self, obs, act, next_obs):
        reward_ctrl = -0.01 * np.sum(np.square(act), axis=-1)
        reward_run = obs['observation'][..., 0]

        # reward_contact = 0.0
        reward_contact = (
            -0.5  * np.sum(np.square(np.clip(self.sim.data.cfrc_ext, -1, 1)))
        )
        reward_survive = 1.0
        reward = reward_run + reward_ctrl + reward_contact + reward_survive

        return reward

    def tf_reward_fn(self):
        def _thunk(obs, act, next_obs):
            reward_ctrl = -0.01 * tf.reduce_sum(tf.square(act), axis=-1)
            reward_run = obs[..., 0]

            # reward_contact = 0.0
            reward_contact = (
            -0.5  * np.sum(np.square(np.clip(self.sim.data.cfrc_ext, -1, 1)))
        )
            reward_survive = 1.0
            reward = reward_run + reward_ctrl + reward_contact + reward_survive
            return reward
        return _thunk

    def change_env(self):
        mass = np.copy(self.original_mass)
        damping = np.copy(self.original_damping)

        mass[2:5] *= self.mass_scale
        mass[5:8] *= self.mass_scale
        mass[8:11] *= 1.0/self.mass_scale
        mass[11:14] *= 1.0/self.mass_scale
        
        damping[2:5] *= self.damping_scale
        damping[5:8] *= self.damping_scale
        damping[8:11] *= 1.0/self.damping_scale
        damping[11:14] *= 1.0/self.damping_scale

        self.model.body_mass[:] = mass
        self.model.dof_damping[:] = damping
    
    def viewer_setup(self):
        self.viewer.cam.distance = self.model.stat.extent * 0.5
    
    def get_sim_parameters(self):
        return np.array([self.mass_scale, self.damping_scale])
    
    def num_modifiable_parameters(self):
        return 2

    def log_diagnostics(self, paths, prefix):
        return
    
    def seed(self, seed=None):
        if seed is None:
            self._seed = 0
        else:
            self._seed = seed
        super().seed(seed)

    def reset(self, *, seed = None, options= None):
        self.current_trajectory_length = 0
        self.current_trajectory_reward = 0
        return super().reset()
    

