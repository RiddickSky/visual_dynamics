from __future__ import division

import numpy as np
import caffe
from caffe import layers as L
from caffe import params as P
from caffe.proto import caffe_pb2 as pb2

def compute_jacobian(net, output, input_):
    assert output in net.outputs
    assert input_ in net.inputs
    input_data = net.blobs[input_].data
    assert input_data.ndim == 2
    assert input_data.shape[0] == 1
    output_data = net.blobs[output].data
    assert output_data.ndim == 2
    assert output_data.shape[0] == 1
    doutput_dinput = np.array([net.backward(y_diff_pred=e[None,:])[input_].flatten() for e in np.eye(output_data.shape[1])])
    return doutput_dinput

def deploy_net(net, inputs, input_shapes, outputs, batch_size=1, force_backward=True):
    # remove data layers and the ones that depend on them (except for inputs) or the output
    layers_to_remove = [layer for layer in net.layer if not layer.bottom]
    for output in outputs:
        layers_to_remove.extend([layer for layer in net.layer if output in layer.bottom])
    for layer_to_remove in layers_to_remove:
        if layer_to_remove not in net.layer:
            continue
        tops_to_remove = set(layer_to_remove.top) - set(inputs)
        net.layer.remove(layer_to_remove)
        for top_to_remove in tops_to_remove:
            layers_to_remove.extend([layer for layer in net.layer if top_to_remove in layer.bottom])

    net.input.extend(inputs)
    net.input_shape.extend([pb2.BlobShape(dim=(batch_size,)+shape) for shape in input_shapes])
    net.force_backward = force_backward
    return net

def approx_bilinear_net(input_shapes, hdf5_txt_fname='', batch_size=1, net_name='ApproxBilinearNet'):
    assert len(input_shapes) == 2
    image_shape, vel_shape = input_shapes
    assert len(image_shape) == 3
    assert len(vel_shape) == 1
    _, height, width = image_shape
    y_dim = height * width

    fc_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=0, decay_mult=0)],
                     num_output=y_dim,
                     weight_filler=dict(type='gaussian', std=0.001),
                     bias_filler=dict(type='constant', value=0))

    n = caffe.NetSpec()
    n.image_curr, n.image_diff, n.vel = L.HDF5Data(name='data', ntop=3, batch_size=batch_size, source=hdf5_txt_fname)
    u = n.vel
    n.y = L.Flatten(n.image_curr, name='flatten1')
    n.y_diff = L.Flatten(n.image_diff, name='flatten2')
    n.fc1_y = L.InnerProduct(n.y, name='fc1', **fc_kwargs)
    n.fc2_u = L.InnerProduct(u, name='fc2', **fc_kwargs)
    n.fc3_u = L.InnerProduct(u, name='fc3', **fc_kwargs)
    n.prod_y_u = L.Eltwise(n.fc1_y, n.fc2_u, name='prod', operation=P.Eltwise.PROD)
    n.y_diff_pred = L.Eltwise(n.prod_y_u, n.fc3_u, name='sum', operation=P.Eltwise.SUM)
    n.loss = L.EuclideanLoss(n.y_diff_pred, n.y_diff, name='loss')

    net = n.to_proto()
    net.name = net_name
    return net

def Bilinear(n, y, u, y_dim, u_dim, name='bilinear', **fc_kwargs):
    re_y = n.tops[name+'_re_y'] = L.Reshape(y, shape=dict(dim=[-1, y_dim, 1]))
    tile_re_y = n.tops[name+'_tile_re_y'] = L.Tile(re_y, axis=2, tiles=u_dim)
    flatten_tile_re_y = n.tops[name+'_flatten_tile_re_y'] = L.Flatten(tile_re_y)
    tile_u = n.tops[name+'_tile_u'] = L.Tile(u, axis=1, tiles=y_dim)
    outer_yu = n.tops[name+'_outer_yu'] = L.Eltwise(flatten_tile_re_y, tile_u, operation=P.Eltwise.PROD)
    fc_outer_yu = n.tops[name+'_fc_outer_yu'] = L.InnerProduct(outer_yu, **fc_kwargs)
    fc_u = n.tops[name+'_fc_u'] = L.InnerProduct(u, **fc_kwargs)
    return L.Eltwise(fc_outer_yu, fc_u, operation=P.Eltwise.SUM)

def bilinear_net(input_shapes, hdf5_txt_fname='', batch_size=1, net_name='BilinearNet'):
    assert len(input_shapes) == 2
    image_shape, vel_shape = input_shapes
    assert len(image_shape) == 3
    assert len(vel_shape) == 1
    _, height, width = image_shape
    y_dim = height * width
    u_dim = vel_shape[0]

    fc_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=0, decay_mult=0)],
                     num_output=y_dim,
                     weight_filler=dict(type='gaussian', std=0.001),
                     bias_filler=dict(type='constant', value=0))

    n = caffe.NetSpec()
    n.image_curr, n.image_diff, n.vel = L.HDF5Data(name='data', ntop=3, batch_size=batch_size, source=hdf5_txt_fname)
    u = n.vel
    n.y = L.Flatten(n.image_curr, name='flatten1')
    n.y_diff_pred = Bilinear(n, n.y, u, y_dim, u_dim, **fc_kwargs)
    n.y_diff = L.Flatten(n.image_diff, name='flatten2')
    n.loss = L.EuclideanLoss(n.y_diff_pred, n.y_diff, name='loss')

    net = n.to_proto()
    net.name = net_name
    return net

def action_cond_encoder_net(input_shapes, hdf5_txt_fname='', batch_size=1, net_name='ActionCondEncoderNet'):
    assert len(input_shapes) == 2
    image_shape, vel_shape = input_shapes
    assert len(image_shape) == 3
    assert len(vel_shape) == 1
    y_dim = 1024
    u_dim = vel_shape[0]

    conv_kwargs = dict(num_output=64, kernel_size=6, stride=2)
    deconv_kwargs = conv_kwargs
    fc_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=0, decay_mult=0)],
                     num_output=y_dim,
                     weight_filler=dict(type='gaussian', std=0.001),
                     bias_filler=dict(type='constant', value=0))

    n = caffe.NetSpec()
    n.image_curr, n.image_diff, n.vel = L.HDF5Data(name='data', ntop=3, batch_size=batch_size, source=hdf5_txt_fname)

    n.conv1 = L.Convolution(n.image_curr, name='conv1', **conv_kwargs)
    n.relu1 = L.ReLU(n.conv1, name='relu1', in_place=True)
    n.conv2 = L.Convolution(n.relu1, name='conv2', pad=2, **conv_kwargs)
    n.relu2 = L.ReLU(n.conv2, name='relu2', in_place=True)
    n.conv3 = L.Convolution(n.relu2, name='conv3', pad=2, **conv_kwargs)
    n.relu3 = L.ReLU(n.conv3, name='relu3', in_place=True)
    n.y = L.InnerProduct(n.relu3, name='ip1', num_output=y_dim, weight_filler=dict(type='xavier'))

    u = n.vel
    n.y_diff_pred = Bilinear(n, y, u, y_dim, u_dim, **fc_kwargs)
    n.y_next_pred = L.Eltwise(y, n.y_diff_pred, operation=P.Eltwise.SUM)

    n.ip2 = L.InnerProduct(n.y_next_pred, name='ip2', num_output=6400, weight_filler=dict(type='xavier'))
    n.re_y_next_pred = L.Reshape(n.ip2, shape=dict(dim=[batch_size, 64, 10, 10]))
    n.deconv3 = L.Deconvolution(n.re_y_next_pred, convolution_param=dict(deconv_kwargs.items() + dict(pad=2).items()))
    n.derelu3 = L.ReLU(n.deconv3, in_place=True)
    n.deconv2 = L.Deconvolution(n.derelu3, convolution_param=dict(deconv_kwargs.items() + dict(pad=2).items()))
    n.derelu2 = L.ReLU(n.deconv2, in_place=True)
    n.deconv1 = L.Deconvolution(n.derelu2, convolution_param=dict(deconv_kwargs.items() + dict(num_output=1).items()))
    n.image_next_pred = L.ReLU(n.deconv1, in_place=True)

    n.image_next = L.Eltwise(n.image_curr, n.image_diff, operation=P.Eltwise.SUM)

    n.loss = L.EuclideanLoss(n.image_next_pred, n.image_next, name='loss')

    net = n.to_proto()
    net.name = net_name
    return net

def small_action_cond_encoder_net(input_shapes, hdf5_txt_fname='', batch_size=1, net_name='SmallActionCondEncoderNet'):
    assert len(input_shapes) == 2
    image_shape, vel_shape = input_shapes
    assert len(image_shape) == 3
    assert len(vel_shape) == 1
    y_dim = 128
    u_dim = vel_shape[0]
    conv_num_output = 16
    conv2_wh = 8

    conv_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=1, decay_mult=1)],
                       convolution_param=dict(num_output=conv_num_output,
                                              kernel_size=6,
                                              stride=2,
                                              pad=2,
                                              weight_filler=dict(type='gaussian', std=0.01),
                                              bias_filler=dict(type='constant', value=0)))
    deconv_kwargs = conv_kwargs
    deconv_kwargs1 = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=1, decay_mult=1)],
                        convolution_param=dict(num_output=1,
                                               kernel_size=6,
                                               stride=2,
                                               pad=2,
                                               weight_filler=dict(type='gaussian', std=0.01),
                                               bias_filler=dict(type='constant', value=0)))
    fc_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=0, decay_mult=0)],
                     num_output=y_dim,
                     weight_filler=dict(type='gaussian', std=0.001),
                     bias_filler=dict(type='constant', value=0))

    n = caffe.NetSpec()
    n.image_curr, n.image_diff, n.vel = L.HDF5Data(name='data', ntop=3, batch_size=batch_size, source=hdf5_txt_fname)

    n.conv1 = L.Convolution(n.image_curr, **conv_kwargs)
    n.relu1 = L.ReLU(n.conv1, name='relu1', in_place=True)
    n.conv2 = L.Convolution(n.relu1, **conv_kwargs)
    n.relu2 = L.ReLU(n.conv2, name='relu2', in_place=True)
    n.y = L.InnerProduct(n.relu2, name='ip1', num_output=y_dim, weight_filler=dict(type='xavier'))

    u = n.vel
    n.y_diff_pred = Bilinear(n, n.y, u, y_dim, u_dim, **fc_kwargs)
    n.y_next_pred = L.Eltwise(n.y, n.y_diff_pred, operation=P.Eltwise.SUM)

    n.ip2 = L.InnerProduct(n.y_next_pred, name='ip2', num_output=conv_num_output*conv2_wh**2, weight_filler=dict(type='xavier'))
    n.re_y_next_pred = L.Reshape(n.ip2, shape=dict(dim=[batch_size, conv_num_output, conv2_wh, conv2_wh]))
    n.deconv2 = L.Deconvolution(n.re_y_next_pred, **deconv_kwargs)
    n.derelu2 = L.ReLU(n.deconv2, in_place=True)
    n.deconv1 = L.Deconvolution(n.derelu2, **deconv_kwargs1)
    n.image_next_pred = L.ReLU(n.deconv1, in_place=True)

    n.image_next = L.Eltwise(n.image_curr, n.image_diff, operation=P.Eltwise.SUM)

    n.loss = L.EuclideanLoss(n.image_next_pred, n.image_next, name='loss')

    net = n.to_proto()
    net.name = net_name
    return net

def downsampled_small_action_cond_encoder_net(input_shapes, hdf5_txt_fname='', batch_size=1, net_name='DownsampledSmallActionCondEncoderNet'):
    assert len(input_shapes) == 2
    image_shape, vel_shape = input_shapes
    assert len(image_shape) == 3
    assert len(vel_shape) == 1
    y_dim = 32
    u_dim = vel_shape[0]
    conv_num_output = 16
    conv2_wh = 4

    blur_conv_kwargs = dict(param=[dict(lr_mult=0, decay_mult=0)],
                            convolution_param=dict(num_output=1,
                                                   kernel_size=5,
                                                   stride=2,
                                                   pad=2,
                                                   bias_term=False))
    conv_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=1, decay_mult=1)],
                       convolution_param=dict(num_output=conv_num_output,
                                              kernel_size=6,
                                              stride=2,
                                              pad=2,
                                              weight_filler=dict(type='gaussian', std=0.01),
                                              bias_filler=dict(type='constant', value=0)))
    deconv_kwargs = conv_kwargs
    deconv_kwargs1 = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=1, decay_mult=1)],
                        convolution_param=dict(num_output=1,
                                               kernel_size=6,
                                               stride=2,
                                               pad=2,
                                               weight_filler=dict(type='gaussian', std=0.01),
                                               bias_filler=dict(type='constant', value=0)))
    fc_kwargs = dict(param=[dict(lr_mult=1, decay_mult=1), dict(lr_mult=0, decay_mult=0)],
                     num_output=y_dim,
                     weight_filler=dict(type='gaussian', std=0.001),
                     bias_filler=dict(type='constant', value=0))

    n = caffe.NetSpec()
    n.image_curr, n.image_diff, n.vel = L.HDF5Data(name='data', ntop=3, batch_size=batch_size, source=hdf5_txt_fname)

    n.image_curr_ds = L.Convolution(n.image_curr, name='blur_conv1', **blur_conv_kwargs)
    n.image_diff_ds = L.Convolution(n.image_diff, name='blur_conv2', **blur_conv_kwargs)

    n.conv1 = L.Convolution(n.image_curr_ds, **conv_kwargs)
    n.relu1 = L.ReLU(n.conv1, name='relu1', in_place=True)
    n.conv2 = L.Convolution(n.relu1, **conv_kwargs)
    n.relu2 = L.ReLU(n.conv2, name='relu2', in_place=True)
    n.y = L.InnerProduct(n.relu2, name='ip1', num_output=y_dim, weight_filler=dict(type='xavier'))

    u = n.vel
    n.y_diff_pred = Bilinear(n, n.y, u, y_dim, u_dim, **fc_kwargs)
    n.y_next_pred = L.Eltwise(n.y, n.y_diff_pred, operation=P.Eltwise.SUM)

    n.ip2 = L.InnerProduct(n.y_next_pred, name='ip2', num_output=conv_num_output*conv2_wh**2, weight_filler=dict(type='xavier'))
    n.re_y_next_pred = L.Reshape(n.ip2, shape=dict(dim=[batch_size, conv_num_output, conv2_wh, conv2_wh]))
    n.deconv2 = L.Deconvolution(n.re_y_next_pred, **deconv_kwargs)
    n.derelu2 = L.ReLU(n.deconv2, in_place=True)
    n.deconv1 = L.Deconvolution(n.derelu2, **deconv_kwargs1)
    n.image_next_pred = L.ReLU(n.deconv1, in_place=True)

    n.image_next = L.Eltwise(n.image_curr_ds, n.image_diff_ds, operation=P.Eltwise.SUM)

    n.loss = L.EuclideanLoss(n.image_next_pred, n.image_next, name='loss')

    net = n.to_proto()
    net.name = net_name
    return net
