from torch import cuda

# This file contains the configuration parameters which will be used throughout your experiments
use_cuda = cuda.is_available()

start_epoch = 1
n_epochs = 300
batch_size = 16
use_instance = True

seg_coef = 1
center_coef = 1
regression_coef = 0.01

model_id = 6
save_dir = './SavedModels/Run%d/' % model_id
save_dir2 = './SavedIterations/Run%d/' % model_id

learning_rate = 1e-3
weight_decay = 1e-7
h = 1024
w = 2048

data_dir = './CityscapesData'

num_workers = 8
n_classes = 34
