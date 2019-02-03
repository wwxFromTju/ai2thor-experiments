"""
Adapted from: https://github.com/ikostrikov/pytorch-a3c/blob/master/main.py
The main file needed within a3c. Runs of the train and test functions from their respective files.
Example of use:
`cd algorithms/a3c`
`python main.py`

Runs A3C on our AI2ThorEnv wrapper with default params (4 processes). Optionally it can be
run on any atari environment as well using the --atari and --atari-env-name params.
"""

from __future__ import print_function

import argparse
import os
import uuid
import glob

import torch
import torch.multiprocessing as mp

from gym_ai2thor.envs.ai2thor_env import AI2ThorEnv
from algorithms.a3c.envs import create_atari_env
from algorithms.a3c import my_optim
from algorithms.a3c.model import ActorCritic, A3C_LSTM_GA
from algorithms.a3c.test import test
from algorithms.a3c.train import train

# Based on
# https://github.com/pytorch/examples/tree/master/mnist_hogwild
# Training settings
parser = argparse.ArgumentParser(description='A3C')
parser.add_argument('--lr', type=float, default=0.0001,
                    help='learning rate (default: 0.0001)')
parser.add_argument('--gamma', type=float, default=0.99,
                    help='discount factor for rewards (default: 0.99)')
parser.add_argument('--tau', type=float, default=1.00,
                    help='parameter for GAE (default: 1.00)')
parser.add_argument('--entropy-coef', type=float, default=0.01,
                    help='entropy term coefficient (default: 0.01)')
parser.add_argument('--value-loss-coef', type=float, default=0.5,
                    help='value loss coefficient (default: 0.5)')
parser.add_argument('--max-grad-norm', type=float, default=50,
                    help='value loss coefficient (default: 50)')
parser.add_argument('--seed', type=int, default=1,
                    help='random seed (default: 1)')
parser.add_argument('--total-length', type=int, default=0,
                    help='initial number of steps if resuming')
parser.add_argument('--test-sleep-time', type=int, default=200,
                    help='number of seconds to wait before testing again (default: 200)')
parser.add_argument('--number-of-episodes', type=int, default=0, help='number-of-episodes passed if resuming')
parser.add_argument('-eid', '--experiment-id', default=uuid.uuid4(),
                    help='random or chosen guid for folder creation for plots and checkpointing.'
                         ' If experiment taken, will resume training!')
parser.add_argument('--num-processes', type=int, default=4,
                    help='how many training processes to use (default: 1)')
parser.add_argument('--num-steps', type=int, default=20,
                    help='number of forward steps in A3C (default: 20)')
parser.add_argument('--max-episode-length', type=int, default=1000,
                    help='maximum length of an episode (default: 1000000)')
parser.add_argument('--natural-language', dest='natural_language', action='store_true',
                    help='env returns natural language sentence as instruction')
parser.set_defaults(natural_language=False)
parser.add_argument('--no-shared', default=False,
                    help='use an optimizer without shared momentum.')
parser.add_argument('-sync', '--synchronous', dest='synchronous', action='store_true',
                    help='Useful for debugging purposes e.g. import pdb; pdb.set_trace(). '
                         'Overwrites args.num_processes as everything is in main thread. '
                         '1 train() function is run and no test()')
parser.add_argument('-async', '--asynchronous', dest='synchronous', action='store_false')
parser.set_defaults(synchronous=False)

# Atari arguments. Good example of keeping code modular and allowing algorithms to run everywhere
parser.add_argument('--atari', dest='atari', action='store_true',
                    help='Run atari env instead with name below instead of ai2thor')
parser.add_argument('--atari-render', dest='atari_render', action='store_true',
                    help='Render atari')
parser.add_argument('--atari-env-name', default='PongDeterministic-v4',
                    help='environment to train on (default: PongDeterministic-v4)')
#
parser.set_defaults(atari=False)
parser.set_defaults(atari_render=False)


if __name__ == '__main__':
    os.environ['OMP_NUM_THREADS'] = '1'  # todo try multiple threads?
    os.environ['CUDA_VISIBLE_DEVICES'] = ""

    # todo print args to file in experiment folder
    # todo print all logs to experiment folder
    # todo allow changing name of experiment folder and perfect checkpointing
    # todo load episode number, learning rate and optimiser and more

    args = parser.parse_args()

    torch.manual_seed(args.seed)
    if args.atari:
        env = create_atari_env(args.atari_env_name)
        args.frame_dim = 42  # fixed to be 42x42 in envs.py _process_frame42()
    else:
        # todo specify lookup, interaction actions to be off.
        args.config_dict = {'max_episode_length': args.max_episode_length,
                            'natural_language_instructions': args.natural_language,
                            "task": {
                                "task_name": "NaturalLanguageLookAtTask"
                            }}
        env = AI2ThorEnv(config_dict=args.config_dict)
        args.frame_dim = env.config['resolution'][-1]

    if args.natural_language:
        # environment will return natural language sentence as part of state so process it with
        # Gated Attention (GA) variant of A3C
        shared_model = A3C_LSTM_GA(env.observation_space.shape[0], env.action_space.n,
                                   args.frame_dim)
    else:
        shared_model = ActorCritic(env.observation_space.shape[0], env.action_space.n,
                                   args.frame_dim)
    shared_model.share_memory()

    env.close()  # above env initialisation was only to find certain params needed

    if args.no_shared:
        optimizer = None
    else:
        optimizer = my_optim.SharedAdam(shared_model.parameters(), lr=args.lr)
        optimizer.share_memory()

    args.experiment_path = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..'
                                                                     , '..', 'experiments',
                                                                     str(args.experiment_id))))
    args.checkpoint_path = os.path.join(args.experiment_path, 'checkpoints')
    args.tensorboard_path = os.path.join(args.experiment_path, 'tensorboard_logs')

    # # Checkpoint
    # if not os.path.exists(args.experiment_path):
    #     print('Creating experiment folder: {}'.format(args.experiment_path))
    #     os.makedirs(args.experiment_path)
    # else:
    #     print('Experiment already exists at path: {}'.format(args.experiment_path))
    #     checkpoint_paths = glob.glob(args.experiment_path + '/checkpoint*')
    #     # Take checkpoint path with most experience
    #     checkpoint_file_name_ints = [int(x.split('/')[-1].split('.pth.tar')[0].split('_')[-1])
    #                                  for x in checkpoint_paths]
    #     idx_of_latest = checkpoint_file_name_ints.index(max(checkpoint_file_name_ints))
    #     checkpoint_to_load = checkpoint_paths[idx_of_latest]
    #     print('Loading latest checkpoint: {}'.format(checkpoint_to_load))
    #
    #     if os.path.isfile(checkpoint_to_load):
    #         print("=> loading checkpoint '{}'".format(checkpoint_to_load))
    #         checkpoint = torch.load(checkpoint_to_load)
    #         args.total_length = checkpoint['total_length']
    #         shared_model.load_state_dict(checkpoint['state_dict'])
    #         optimizer.load_state_dict(
    #             checkpoint['optimizer'])  # todo check if overwrites learning rate. probably does
    #
    #         if checkpoint['number_of_episodes']:
    #             args.number_of_episodes = checkpoint['number_of_episodes']
    #
    #         if checkpoint['counter']:
    #             checkpoint_counter = checkpoint['counter']
    #
    #         for param_group in optimizer.param_groups:
    #             print('Learning rate: ', param_group['lr'])  # oh it doesn't
    #
    #         print("=> loaded checkpoint '{}' (total_length {})"
    #               .format(checkpoint_to_load, checkpoint['total_length']))
    #
    # # todo have choice of checkpoint as well? args.resume could override the above

    # Checkpoint creation/loading below
    checkpoint_counter = False
    if not os.path.exists(args.checkpoint_path):
        print(
            'Tensorboard created experiment folder: {} and checkpoint folder made here: {}'.format(
                args.experiment_path, args.checkpoint_path))
        os.makedirs(args.checkpoint_path)
    else:
        print('Checkpoints path already exists at path: {}'.format(args.checkpoint_path))
        checkpoint_paths = glob.glob(os.path.join(args.checkpoint_path, '*'))
        if checkpoint_paths:
            # Take checkpoint path with most experience e.g. 2000 from checkpoint_total_length_2000.pth.tar
            checkpoint_file_name_ints = [
                int(x.split('/')[-1].split('.pth.tar')[0].split('_')[-1])
                for x in checkpoint_paths]
            idx_of_latest = checkpoint_file_name_ints.index(max(checkpoint_file_name_ints))
            checkpoint_to_load = checkpoint_paths[idx_of_latest]
            print('Loading latest checkpoint: {}'.format(checkpoint_to_load))

            if os.path.isfile(checkpoint_to_load):
                print("=> loading checkpoint '{}'".format(checkpoint_to_load))
                checkpoint = torch.load(checkpoint_to_load)
                args.total_length = checkpoint['total_length']
                args.episode_number = checkpoint['episode_number']
                print('Values from checkpoint: total_length: {}. episode_number: {}'.format(
                    checkpoint['total_length'], checkpoint['episode_number']))
                shared_model.load_state_dict(checkpoint['state_dict'])
                optimizer.load_state_dict(
                    checkpoint[
                        'optimizer'])  # todo check if overwrites learning rate. probably does

                for param_group in optimizer.param_groups:
                    print('Learning rate: ', param_group['lr'])  # oh it doesn't work?

                print("=> loaded checkpoint '{}' (total_length {})"
                      .format(checkpoint_to_load, checkpoint['total_length']))
        else:
            print('No checkpoint to load')
        # todo have choice of checkpoint as well? args.resume could override the above

    processes = []
    counter = mp.Value('i', 0 if not checkpoint_counter else checkpoint_counter)
    lock = mp.Lock()

    if not args.synchronous:
        # test runs continuously and if episode ends, sleeps for args.test_sleep_time seconds
        p = mp.Process(target=test, args=(args.num_processes, args, shared_model, counter))
        p.start()
        processes.append(p)

        for rank in range(0, args.num_processes):
            p = mp.Process(target=train, args=(rank, args, shared_model, counter, lock, optimizer))
            p.start()
            processes.append(p)
        for p in processes:
            p.join()
    else:
        rank = 0
        # test(args.num_processes, args, shared_model, counter)  # for checking test functionality
        train(rank, args, shared_model, counter, lock, optimizer)  # run train on main thread
