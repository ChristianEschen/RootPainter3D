import os
from setuptools import setup, find_packages

from setuptools.command.install import install

class CustomInstallCommand(install):
    def run(self):
        install.run(self)
        os.system('cp ./bin/main ' + self.install_scripts)

setup(
    # Your package metadata and dependencies
    name='painter app',  # Replace this with your package's name
    version='1.0',
    description='painter',
    author='ChristianEschen',
    author_email='christian_eschen@hotmail.com',
    url='https://github.com/ChristianEschen/RootPainter3D',
    packages=find_packages(),
    cmdclass={'install': CustomInstallCommand},
)
