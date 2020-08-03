import argparse
import pdb
import json

import torch
from torch.utils.data import DataLoader

from models import BlindStatelessLSTM
from tools import (SIMMCDatasetForActionPrediction, SIMMCFashionConfig,
                   TrainConfig, plotting_loss)

"""expected form for model output
    [
        {
            "dialog_id": ...,  
            "predictions": [
                {
                    "action": <predicted_action>,
                    "action_log_prob": {
                        <action_token>: <action_log_prob>,
                        ...
                    },
                    "attributes": {
                        <attribute_label>: <attribute_val>,
                        ...
                    }
                }
            ]
        }
    ]

    Where <attribute_label> is "focus" or "attributes" (only "attributes" for fashion dataset).
"""

id2act = SIMMCDatasetForActionPrediction._LABEL2ACT
id2arg = SIMMCDatasetForActionPrediction._ARGS


def create_eval_dict(dataset):
    eval_dict = {}
    for dial_id in dataset.id2dialog:
        num_turns = len(dataset.id2dialog[dial_id]['dialogue'])
        eval_dict[dial_id] = {'dialog_id': dial_id, 'predictions': [None] * num_turns}
    return eval_dict


def eval(model, test_dataset, args, device):

    model.eval()
    model.to(device)
    print('MODEL: {}'.format(model))

    # prepare DataLoader
    params = {'batch_size': 1,
            'shuffle': False,
            'num_workers': 0}
    testloader = DataLoader(test_dataset, **params, collate_fn=model.collate_fn)

    eval_dict = create_eval_dict(test_dataset)
    with torch.no_grad():
        for curr_step, (dial_ids, turns, batch, seq_lengths, actions, args) in enumerate(testloader):
            assert len(dial_ids) == 1, 'Only unitary batch size is allowed during testing'
            dial_id = dial_ids[0]
            turn = turns[0]

            batch = batch.to(device)
            actions = actions.to(device)
            args = args.to(device)

            actions_out, args_out, actions_probs, args_probs = model(batch, seq_lengths)

            #get predicted action and arguments
            actions_predictions = torch.argmax(actions_probs, dim=-1)
            args_predictions = []
            for batch_idx, t in enumerate(args_probs):
                args_predictions.append([])
                for pos, val in enumerate(t):
                    if val >= .5:
                        args_predictions[batch_idx].append(pos)

            actions_predictions = actions_predictions[0].item()
            args_predictions = args_predictions[0]

            predicted_action = SIMMCDatasetForActionPrediction._LABEL2ACT[actions_predictions]
            action_log_prob = {}
            for idx, prob in enumerate(actions_probs[0]):
                action_log_prob[SIMMCDatasetForActionPrediction._LABEL2ACT[idx]] = torch.log(prob).item()
            attributes = {}
            for arg in args_predictions:
                attributes['attributes'] = SIMMCDatasetForActionPrediction._ARGS[arg]
            
            eval_dict[dial_id]['predictions'][turn] = {'action': predicted_action, 
                                                        'action_log_prob': action_log_prob, 
                                                        'attributes': attributes}

    eval_list = []
    for key in eval_dict:
        eval_list.append(eval_dict[key])
    try:
        with open('model_out2.json', 'w+') as fp:
            json.dump(eval_list, fp)
    except:
        pdb.set_trace()



if __name__ == '__main__':
    #TODO make "infer": dataset with unknown labels (modify the dataset class)
    """Example

        python main.py \
        --model checkpoints/2020-08-02T21:47:10\
        --data ../simmc/data/simmc_fashion/fashion_devtrest_dials.json \
        --metadata ../simmc/data/simmc_fashion/fashion_metadata.json \
        --actions annotations/fashion_devtest_dials_api_calls.json \
        --cuda 0
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model",
        default=None,
        type=str,
        required=True,
        help="Path to the model to use")
    parser.add_argument(
        "--vocabulary",
        default=None,
        type=str,
        required=True,
        help="Path to the vocabulary pickle file")        
    parser.add_argument(
        "--data",
        default=None,
        type=str,
        required=True,
        help="Path to training dataset json file")
    parser.add_argument(
        "--metadata",
        default=None,
        type=str,
        required=True,
        help="Path to metadata json file")
    parser.add_argument(
        "--embeddings",
        default=None,
        type=str,
        required=True,
        help="Path to embedding file")
    parser.add_argument(
        "--actions",
        default=None,
        type=str,
        required=True,
        help="Path to training action annotations file")
    parser.add_argument(
        "--cuda",
        default=None,
        required=False,
        type=int,
        help="id of device to use")

    args = parser.parse_args()
    test_dataset = SIMMCDatasetForActionPrediction(data_path=args.data, metadata_path=args.metadata, actions_path=args.actions)
    device = torch.device('cuda:{}'.format(args.cuda) if torch.cuda.is_available() and args.cuda is not None else "cpu")

    eval_dict = create_eval_dict(test_dataset)
    print('EVAL DATASET: {}'.format(test_dataset))

    # prepare model
    vocabulary_test = test_dataset.get_vocabulary()
    print('VOCABULARY SIZE: {}'.format(len(vocabulary_test)))

    word2id = torch.load(args.vocabulary)

    model = BlindStatelessLSTM(args.embeddings, 
                                word2id=word2id, 
                                OOV_corrections=False, 
                                num_actions=SIMMCFashionConfig._FASHION_ACTION_NO,
                                num_args=SIMMCFashionConfig._FASHION_ARGS_NO,
                                pad_token=TrainConfig._PAD_TOKEN,
                                unk_token=TrainConfig._UNK_TOKEN)
    model.load_state_dict(torch.load(args.model))

    eval(model, test_dataset, args, device)
