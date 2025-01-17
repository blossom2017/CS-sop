"""
Reads Darknet19 config and weights and creates Keras model with TF backend.
Currently only supports layers in Darknet19 config.
"""

import argparse
import configparser
import io
import os
from collections import defaultdict

import numpy as np
from keras import backend as K
from keras.layers import (Conv2D, GlobalAveragePooling2D, Input, Lambda,
                          MaxPooling2D)
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.merge import concatenate
from keras.layers.normalization import BatchNormalization
from keras.models import Model
from keras.regularizers import l2
from keras.utils.vis_utils import plot_model as plot





def unique_config_sections(config_file):
    """Convert all config sections to have unique names.
    Adds unique suffixes to config sections for compability with configparser.
    """
    """Convert all config sections to have unique names.
    Adds unique suffixes to config sections for compability with configparser.
    """
    """Convert all config sections to have unique names.
    Adds unique suffixes to config sections for compability with configparser.
    """
    section_counters = defaultdict(int)
    output_stream = io.StringIO()
    with open(config_file) as fin:
        for line in fin:
            if line.startswith('['):
                section = line.strip().strip('[]')
                _section = section + '_' + str(section_counters[section])
                section_counters[section] += 1
                line = line.replace(section, _section)
            output_stream.write(line)
    output_stream.seek(0)
    return output_stream


# %%
##add the name of configuration file
config_path = 'yolov2-tiny-voc.cfg'

##add the name of weights file 
weights_path = 'yolov2-tiny-voc.weights'

##name of the model file that will be generated and saved
output_path = 'model_keras.h5'

print('Loading weights.')

data = np.fromfile(weights_path,np.float32)
data=data[4:]
print(len(data))

weights_file = open(weights_path, 'rb')

weights_header = np.ndarray(
    shape=(4, ), dtype='int32', buffer=weights_file.read(16))
print('Weights Header: ', weights_header)
# TODO: Check transpose flag when implementing fully connected layers.
# transpose = (weight_header[0] > 1000) or (weight_header[1] > 1000)

print('Parsing Darknet config.')
unique_config_file = unique_config_sections(config_path)
cfg_parser = configparser.ConfigParser()
cfg_parser.read_file(unique_config_file)

print('Creating Keras model.')

image_height = int(cfg_parser['net_0']['height'])
image_width = int(cfg_parser['net_0']['width'])
prev_layer = Input(shape=(image_height, image_width, 3))
all_layers = [prev_layer]

weight_decay = float(cfg_parser['net_0']['decay']
                     ) if 'net_0' in cfg_parser.sections() else 5e-4
count = 0
for section in cfg_parser.sections():
    print('Parsing section {}'.format(section))
    if section.startswith('convolutional'):
        filters = int(cfg_parser[section]['filters'])
        size = int(cfg_parser[section]['size'])
        stride = int(cfg_parser[section]['stride'])
        pad = int(cfg_parser[section]['pad'])
        activation = cfg_parser[section]['activation']
        batch_normalize = 'batch_normalize' in cfg_parser[section]

        # padding='same' is equivalent to Darknet pad=1
        padding = 'same' if pad == 1 else 'valid'

        # Setting weights.
        # Darknet serializes convolutional weights as:
        # [bias/beta, [gamma, mean, variance], conv_weights]
        prev_layer_shape = K.int_shape(prev_layer)

        weights_shape = (size, size, prev_layer_shape[-1], filters)
        darknet_w_shape = (filters, weights_shape[2], size, size)
        weights_size = np.product(weights_shape)

        print('conv2d', 'bn'
              if batch_normalize else '  ', activation, weights_shape)

        conv_bias = np.ndarray(
            shape=(filters, ),
            dtype='float32',
            buffer=weights_file.read(filters * 4))
        count += filters

        if batch_normalize:
            bn_weights = np.ndarray(
                shape=(3, filters),
                dtype='float32',
                buffer=weights_file.read(filters * 12))
            count += 3 * filters

            bn_weight_list = [
                bn_weights[0],  # scale gamma
                conv_bias,  # shift beta
                bn_weights[1],  # running mean
                bn_weights[2]  # running var
            ]

        conv_weights = np.ndarray(
            shape=darknet_w_shape,
            dtype='float32',
            buffer=weights_file.read(weights_size * 4))
        count += weights_size

        # DarkNet conv_weights are serialized Caffe-style:
        # (out_dim, in_dim, height, width)
        # We would like to set these to Tensorflow order:
        # (height, width, in_dim, out_dim)
        # TODO: Add check for Theano dim ordering.
        conv_weights = np.transpose(conv_weights, [2, 3, 1, 0])
        conv_weights = [conv_weights] if batch_normalize else [
            conv_weights, conv_bias
        ]

        # Handle activation.
        act_fn = None
        if activation == 'leaky':
            pass  # Add advanced activation later.
        elif activation != 'linear':
            raise ValueError(
                'Unknown activation function `{}` in section {}'.format(
                    activation, section))

        # Create Conv2D layer
        conv_layer = (Conv2D(
            filters, (size, size),
            strides=(stride, stride),
            kernel_regularizer=l2(weight_decay),
            use_bias=not batch_normalize,
            weights=conv_weights,
            activation=act_fn,
            padding=padding))(prev_layer)

        if batch_normalize:
            conv_layer = (BatchNormalization(
                weights=bn_weight_list))(conv_layer)
        prev_layer = conv_layer

        if activation == 'linear':
            all_layers.append(prev_layer)
        elif activation == 'leaky':
            act_layer = LeakyReLU(alpha=0.1)(prev_layer)
            prev_layer = act_layer
            all_layers.append(act_layer)

    elif section.startswith('maxpool'):
        size = int(cfg_parser[section]['size'])
        stride = int(cfg_parser[section]['stride'])
        all_layers.append(
            MaxPooling2D(
                padding='same',
                pool_size=(size, size),
                strides=(stride, stride))(prev_layer))
        prev_layer = all_layers[-1]

    elif section.startswith('avgpool'):
        if cfg_parser.items(section) != []:
            raise ValueError('{} with params unsupported.'.format(section))
        all_layers.append(GlobalAveragePooling2D()(prev_layer))
        prev_layer = all_layers[-1]

    elif section.startswith('route'):
        ids = [int(i) for i in cfg_parser[section]['layers'].split(',')]
        layers = [all_layers[i] for i in ids]
        if len(layers) > 1:
            print('Concatenating route layers:', layers)
            concatenate_layer = concatenate(layers)
            all_layers.append(concatenate_layer)
            prev_layer = concatenate_layer
        else:
            skip_layer = layers[0]  # only one layer to route
            all_layers.append(skip_layer)
            prev_layer = skip_layer

    elif section.startswith('reorg'):
        block_size = int(cfg_parser[section]['stride'])
        assert block_size == 2, 'Only reorg with stride 2 supported.'
        all_layers.append(
            Lambda(
                space_to_depth_x2,
                output_shape=space_to_depth_x2_output_shape,
                name='space_to_depth_x2')(prev_layer))
        prev_layer = all_layers[-1]

    elif section.startswith('region'):
        with open('{}_anchors.txt'.format(output_path), 'w') as f:
            print(cfg_parser[section]['anchors'])

    elif (section.startswith('net') or section.startswith('cost') or
          section.startswith('softmax')):
        pass  # Configs not currently handled during model definition.

    else:
        raise ValueError(
            'Unsupported section header type: {}'.format(section))

# Create and save model.
model = Model(inputs=all_layers[0], outputs=all_layers[-1])
print(model.summary())

model.save('{}'.format(output_path))
print('Saved Keras model to {}'.format(output_path))
# Check to see if all weights have been read.
remaining_weights = len(weights_file.read()) / 4
weights_file.close()
print('Read {} of {} from Darknet weights.'.format(count, count +
                                                   remaining_weights))
if remaining_weights > 0:
    print('Warning: {} unused weights----something incorrect'.format(remaining_weights))
else :
	print('No of parameters in model succesfully matched with weights file')    



