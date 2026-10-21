"""Experimental #984 affine neutralization proof; no production callers.

Independent math, frozen before measurements. No vendor implementation bundled.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import gaussian_filter

DELTA = 1e-4
VERSION = 'positive-neutral-affine-c1-v1'
MAX_SLOPE = 1e6
MAX_CURVATURE = 1e12


def parameters(gain, offset):
    g, b = float(gain), float(offset)
    if not np.isfinite(g) or not np.isfinite(b) or g <= 0:
        raise ValueError('finite positive gain and finite offset required')
    p = {'gain': g, 'offset': b}
    if b < 0:
        r = max(-b/g, DELTA/g)
        h = DELTA + (1-DELTA)*(g*r+b)
        q = (1-DELTA)*g
        if not (0 < r < 1 and h > 0):
            raise ValueError('no normalized downward join')
        d = h/r
        if q <= 2.5*d:
            a = (3*d-q)/2
            w = r
            curvature = 2*abs(q-a)/r
            kind = 'cubic'
        else:
            a = d/4
            w = 3*(h-r*a)/(q-a)
            curvature = 2*(q-a)/w
            kind = 'shoulder'
        slope = max(a,q)
        p.update(kind=kind, join=r, height=h, slope=q, a=a, width=w)
        endpoint = DELTA+(1-DELTA)*(g+b)
    elif b > 0:
        r = min(max(b/g, DELTA/g),1)
        slope, curvature = g+1.5*b/r, 6*b/r**2
        endpoint = g+b
        p.update(kind='upward',join=r)
    else:
        slope, curvature, endpoint = g, 0., g
        p.update(kind='gain')
    if slope > MAX_SLOPE or curvature > MAX_CURVATURE or not 0 < endpoint <= 1:
        raise ValueError('frozen slope/curvature/output-range guard')
    return {**p, 'max_slope':slope, 'max_curvature':curvature}


def affine_positive(values, gain, offset):
    x = np.asarray(values,dtype=np.float64)
    if np.any(~np.isfinite(x)) or np.any((x<0)|(x>1)):
        raise ValueError('finite normalized values required')
    p = parameters(gain,offset)
    g,b = p['gain'],p['offset']
    if b < 0:
        r,q,a,w = p['join'],p['slope'],p['a'],p['width']
        low = x <= r
        result = DELTA+(1-DELTA)*(g*x+b)
        z = x[low]
        if p['kind']=='cubic':
            result[low] = a*z+(q-a)*z**3/(3*r*r)
        else:
            u = np.maximum((z-r+w)/w,0)
            result[low] = a*z+(q-a)*w*u**3/3
    elif b > 0:
        u = np.minimum(x/p['join'],1)
        result = g*x+b*u*u*(3-2*u)
    else:
        result = g*x
    result = np.where(x==0,0,result)
    if np.any(~np.isfinite(result)) or np.any((result<0)|(result>1)):
        raise ValueError('transfer output outside normalized range')
    result = result.astype(np.float32)
    underflow = (x>0)&(result==0)
    result[underflow] = np.nextafter(np.float32(0),np.float32(1))
    return result, {**p,'positive_underflow_count':int(underflow.sum())}


def signal_weight(data):
    """Same input-derived protection mask as the existing wrapper."""
    lum = .2126*data[...,0]+.7152*data[...,1]+.0722*data[...,2]
    sample=lum.ravel().copy()
    for _ in range(5):
        med,sd=float(np.median(sample)),float(sample.std())
        sample=sample[sample < med+2.5*sd]
        if not sample.size:
            raise ValueError('empty sigma-clipped signal population')
    sky,sd=float(np.median(sample)),float(sample.std())
    lo,hi=sky+1.5*sd,sky+5*sd
    t=np.clip((lum-lo)/max(hi-lo,1e-6),0,1)
    return (1-gaussian_filter(t*t*(3-2*t),3)).astype(np.float32)


def corner_medians(data):
    h,w,_=data.shape
    side=max(h//20,w//20,50)
    samples=np.concatenate([data[:side,:side].reshape(-1,3),data[:side,-side:].reshape(-1,3),
                            data[-side:,:side].reshape(-1,3),data[-side:,-side:].reshape(-1,3)])
    return np.median(samples,axis=0)


def freeze_plan(data, rect):
    """Freeze affine statistics on the unchanged baseline, before candidates."""
    from setiastro.saspro.backgroundneutral import background_neutralize_rgb
    x=np.asarray(data,dtype=np.float32)
    if x.ndim!=3 or x.shape[-1]!=3 or np.any(~np.isfinite(x)) or np.any((x<0)|(x>1)):
        raise ValueError('finite normalized HWC RGB required')
    rx,ry,rw,rh=rect
    if min(rx,ry)<0 or min(rw,rh)<=0 or rx+rw>x.shape[1] or ry+rh>x.shape[0]:
        raise ValueError('rectangle outside input')
    pedestal=x.reshape(-1,3).min(axis=0)
    removed=np.clip(x-pedestal,0,1).astype(np.float32)
    med=np.median(removed[ry:ry+rh,rx:rx+rw],axis=(0,1)).astype(np.float32)
    target=float(np.mean(med))
    gains=np.clip((1-target)/np.maximum(1-med,1e-8),0,10).astype(np.float32)
    vendor=background_neutralize_rgb(x,rect_xywh=tuple(rect),mode='pivot1')
    corners=corner_medians(vendor)
    reference=float(corners.min())
    offsets=corners-reference if (float(corners.max())-reference)/max(reference,1e-5)>.05 else np.zeros(3)
    weight=signal_weight(x)
    shifted=(vendor-offsets).astype(np.float32)
    baseline=np.clip(weight[...,None]*shifted+(1-weight[...,None])*x,0,1).astype(np.float32)
    plan={'version':VERSION,'rect_xywh':list(rect),'mode':'scale','vendor_mode':'pivot1',
          'lo_sigma':1.5,'hi_sigma':5.,'feather':3.,'pedestal':pedestal.tolist(),
          'patch_medians':med.tolist(),'gray_target':target,'pivot_gains':gains.tolist(),
          'corner_medians':corners.tolist(),'corner_offsets':offsets.tolist()}
    return plan,weight,{'pedestal':removed,'vendor':vendor,'corner':shifted,'wrapper':baseline}


def execute(data,plan,weight,sky):
    """Apply frozen parameters; each boundary refuses incremental clipping."""
    original=np.asarray(data,dtype=np.float32)
    if plan['version']!=VERSION or weight.shape!=original.shape[:2] or sky.shape!=weight.shape:
        raise ValueError('plan/mask shape or version mismatch')
    if not sky.any() or np.any(~np.isfinite(weight)) or np.any((weight<0)|(weight>1)):
        raise ValueError('invalid sky or protection mask')
    current=original.copy(); stages={}; records=[]
    for name,gains,offsets in [
        ('pedestal',[1.]*3,[-v for v in plan['pedestal']]),
        ('vendor',plan['pivot_gains'],[1-v for v in plan['pivot_gains']]),
        ('corner',[1.]*3,[-v for v in plan['corner_offsets']])]:
        corrected=np.empty_like(current)
        channels=[]
        for c in range(3):
            corrected[...,c],p=affine_positive(current[...,c],gains[c],offsets[c])
            channels.append(p)
        introduced=(current>0)&(corrected<=0)
        fraction=np.mean(np.any(introduced[sky],axis=-1))
        if fraction!=0:
            raise ValueError('incremental clipping ceiling exceeded')
        stages[name]=corrected.copy()
        records.append({'stage':name,'channels':channels,'incremental_any_sky_fraction':float(fraction)})
        current=corrected
    final=weight[...,None]*current+(1-weight[...,None])*original
    final[original==0]=0
    if np.any(~np.isfinite(final)) or np.any((final<0)|(final>1)) or np.any((original[sky]>0)&(final[sky]<=0)):
        raise ValueError('final blend range/finite/clipping guard')
    stages['wrapper']=final.astype(np.float32)
    return stages,records
