sudo docker build -t painter_paint_singularity .
​
sudo docker save -o singularity.tar painter_paint_singularity
​
​
sudo singularity build --sandbox s_container docker-archive://singularity.tar
​
sudo singularity build painter_singularity.sif s_container
​
​
SINGULARITY_TMPDIR=$PWD/singularity/tmp SINGULARITY_CACHEDIR=$PWD/singularity/cache singularity shell --nv -B $PWD:$PWD /home/alatar/fork_dcm_rootpainter/RootPainter3D/painter/painter_singularity.sif
​