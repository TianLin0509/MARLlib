from ray.rllib.utils.torch_ops import apply_grad_clipping, sequence_mask
from ray.rllib.models.action_dist import ActionDistribution
from ray.rllib.models.modelv2 import ModelV2
from ray.rllib.policy.policy import Policy
from ray.rllib.utils.typing import TrainerConfigDict, TensorType, \
    LocalOptimizer, GradInfoDict
from ray.rllib.utils.framework import try_import_torch
from ray.rllib.agents.a3c.a3c_torch_policy import A3CTorchPolicy, actor_critic_loss
from ray.rllib.agents.a3c.a2c import A2C_DEFAULT_CONFIG as A2C_CONFIG, A2CTrainer
from ray.rllib.evaluation.postprocessing import compute_advantages, Postprocessing
from ray.rllib.policy.sample_batch import SampleBatch
from ray.rllib.utils.torch_ops import convert_to_torch_tensor
from typing import Dict, Tuple
from marl.algos.utils.postprocessing import CentralizedValueMixin, centralized_critic_postprocessing

torch, nn = try_import_torch()


############
### COMA ###
############

def central_critic_coma_loss(policy: Policy, model: ModelV2,
                             dist_class: ActionDistribution,
                             train_batch: SampleBatch) -> TensorType:
    CentralizedValueMixin.__init__(policy)
    logits, _ = model.from_batch(train_batch)
    opp_action_in_cc = policy.config["model"]["custom_model_config"]["opp_action_in_cc"]
    values = model.central_value_function(convert_to_torch_tensor(
        train_batch["state"], policy.device),
        convert_to_torch_tensor(
            train_batch["opponent_actions"], policy.device) if opp_action_in_cc else None)
    pi = torch.nn.functional.softmax(logits, dim=-1)

    if policy.is_recurrent():
        B = len(train_batch[SampleBatch.SEQ_LENS])
        max_seq_len = logits.shape[0] // B
        mask_orig = sequence_mask(train_batch[SampleBatch.SEQ_LENS],
                                  max_seq_len)
        valid_mask = torch.reshape(mask_orig, [-1])
    else:
        valid_mask = torch.ones_like(values, dtype=torch.bool)

    dist = dist_class(logits, model)
    log_probs = dist.logp(train_batch[SampleBatch.ACTIONS]).reshape(-1)

    # here the coma loss & calculate the mean values as baseline:
    select_action_Q_value = values.gather(1, train_batch[SampleBatch.ACTIONS].unsqueeze(1)).squeeze()
    advantages = (select_action_Q_value - torch.sum(values * pi, dim=1)).detach()
    coma_pi_err = -torch.sum(torch.masked_select(log_probs * advantages, valid_mask))

    # Compute coma critic loss.
    if policy.config["use_critic"]:
        value_err = 0.5 * torch.sum(
            torch.pow(
                torch.masked_select(
                    select_action_Q_value.reshape(-1) -
                    train_batch[Postprocessing.VALUE_TARGETS], valid_mask),
                2.0))
    # Ignore the value function.
    else:
        value_err = 0.0

    entropy = torch.sum(torch.masked_select(dist.entropy(), valid_mask))

    total_loss = (coma_pi_err + value_err * policy.config["vf_loss_coeff"] -
                  entropy * policy.config["entropy_coeff"])

    # Store values for stats function in model (tower), such that for
    # multi-GPU, we do not override them during the parallel loss phase.
    model.tower_stats["entropy"] = entropy
    model.tower_stats["pi_err"] = coma_pi_err
    model.tower_stats["value_err"] = value_err

    return total_loss


def coma_model_value_predictions(
        policy: Policy, input_dict: Dict[str, TensorType], state_batches,
        model: ModelV2,
        action_dist: ActionDistribution) -> Dict[str, TensorType]:
    return {SampleBatch.VF_PREDS: model.value_function()}


COMATorchPolicy = A3CTorchPolicy.with_updates(
    name="COMATorchPolicy",
    get_default_config=lambda: A2C_CONFIG,
    postprocess_fn=centralized_critic_postprocessing,
    loss_fn=central_critic_coma_loss,
    extra_action_out_fn=coma_model_value_predictions,
    mixins=[
        CentralizedValueMixin
    ])


def get_policy_class_coma(config_):
    if config_["framework"] == "torch":
        return COMATorchPolicy


COMATrainer = A2CTrainer.with_updates(
    name="COMATrainer",
    default_policy=None,
    get_policy_class=get_policy_class_coma,
)
