from ray import tune
from ray.tune.utils import merge_dicts
from ray.tune import CLIReporter
from marl.algos.core.CC.maddpg import MADDPGRNNTrainer as MADDPGTrainer


def run_maddpg(config_dict, common_config, env_dict, stop):
    train_batch_size = config_dict["algo_args"]["batch_episode"]
    buffer_size = config_dict["algo_args"]["buffer_size"]
    episode_limit = env_dict["episode_limit"]
    algorithm = config_dict["algorithm"]
    batch_mode = config_dict["algo_args"]["batch_mode"]
    lr = config_dict["algo_args"]["lr"]
    learning_starts = episode_limit * train_batch_size

    config = {
        "batch_mode": batch_mode,
        "buffer_size": buffer_size,
        "train_batch_size": train_batch_size,
        "critic_lr": lr,
        "actor_lr": lr,
        "model": {
            "max_seq_len": episode_limit,
            "custom_model_config": merge_dicts(config_dict, env_dict),
        },
        "prioritized_replay": True,
        "zero_init_states": True,
        "learning_starts": learning_starts
    }
    config.update(common_config)

    results = tune.run(MADDPGTrainer, name=algorithm + "_" + config_dict["model_arch_args"]["core_arch"] + "_" +
                                         config_dict["env_args"][
                                             "map_name"],
                       stop=stop,
                       config=config,
                       verbose=1,
                       progress_reporter=CLIReporter())

    return results
