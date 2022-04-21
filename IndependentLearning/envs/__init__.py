from IndependentLearning.envs.mpe_rllib import RllibMPE
from IndependentLearning.envs.mamujoco_rllib import RllibMAMujoco
from IndependentLearning.envs.smac_rllib import RLlibSMAC
from IndependentLearning.envs.football_rllib import RllibGFootball
from IndependentLearning.envs.rware_rllib import RllibRWARE

REGISTRY = {}

REGISTRY["mpe"] = RllibMPE
REGISTRY["rware"] = RllibRWARE
REGISTRY["mamujoco"] = RllibMAMujoco
REGISTRY["smac"] = RLlibSMAC
REGISTRY["football"] = RllibGFootball