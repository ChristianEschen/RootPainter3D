FROM ubuntu:22.04 as base
ARG DEBIAN_FRONTEND=noninteractive
COPY requirements.txt  /tmp/


# postgres
RUN apt-get update -y && \ 
   apt-get install tk-dev -y && \
   rm -r /var/lib/apt/lists/* && \
   apt-get update -y && \ 
   apt-get install libpq-dev -y

RUN apt-get update && apt-get install -y \
    git \
    git-lfs

RUN apt-get install python3-pip -y
#RUN apt install libcb-util0
# pip packages
RUN pip3 install --requirement /tmp/requirements.txt

RUN git clone https://github.com/ChristianEschen/RootPainter3D

# fix qt
RUN apt-get update && \
    apt-get install -y libqt5gui5 && \
    rm -rf /var/lib/apt/lists/*
ENV QT_DEBUG_PLUGINS=1