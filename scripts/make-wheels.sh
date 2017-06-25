set -ex

SRC=/app/src
DST=/app/dst

function build_wheel() {
	/opt/python/$1/bin/pip install -Ur ${SRC}/requirements.txt
	/opt/python/$1/bin/pip wheel ${SRC} -f ${SRC} -w ${DST}
}


build_wheel cp34-cp34m
build_wheel cp35-cp35m
build_wheel cp36-cp36m


cd ${DST}
for f in ./*linux_*; do if [ -f $f ]; then auditwheel repair $f -w . ; rm $f; fi; done
cd -

