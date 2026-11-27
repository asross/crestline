#!/bin/bash
# Spot EC2 runners for the bench workflows' `ec2` arch (8+ core Graviton).
#
#   bench/ec2_runner.sh up N [TYPE] [HOURS]   start N one-job runners (c8g.2xlarge, 6 h cap)
#   bench/ec2_runner.sh ls                    list running bench instances
#   bench/ec2_runner.sh down                  terminate all bench instances
#
# Then run bench-std / bench-paired with archs=ec2 and shards=N. Each
# instance registers a just-in-time runner (label crest-ec2) that takes one
# job and exits; the instance then shuts down, which terminates it. A
# shutdown timer at boot caps every instance at HOURS whatever happens.
# Needs the AWS profile crestline-bench (EC2 spot, tag Project=crestline-bench
# only) and gh with admin on asross/crestline. No inbound ports; no long-lived
# GitHub credential on the instance.
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-crestline-bench}
REPO=asross/crestline
TAG='Name=tag:Project,Values=crestline-bench'
gh_() { perl -e 'alarm 60; exec @ARGV' gh "$@"; }

case ${1:-} in
up)
  n=${2:?count}; type=${3:-c8g.2xlarge}; hours=${4:-6}
  ami=$(aws ec2 describe-images --owners 099720109477 --output text \
    --filters 'Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*' \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId')
  ver=$(gh_ api repos/actions/runner/releases/latest --jq '.tag_name|ltrimstr("v")')
  for i in $(seq "$n"); do
    jit=$(gh_ api -X POST repos/$REPO/actions/runners/generate-jitconfig -f name="crest-ec2-$(date +%s)-$i" \
      -F runner_group_id=1 -f 'labels[]=crest-ec2' -f work_folder=_work --jq .encoded_jit_config)
    ud=$(mktemp)
    cat > "$ud" <<EOF
#!/bin/bash
shutdown -h +$((hours * 60))
useradd -m runner && echo 'runner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/runner
cd /home/runner && curl -sfL --retry 5 https://github.com/actions/runner/releases/download/v$ver/actions-runner-linux-arm64-$ver.tar.gz | tar xz
./bin/installdependencies.sh && chown -R runner: /home/runner
sudo -iu runner bash -c 'cd /home/runner && ./run.sh --jitconfig $jit'
shutdown -h now
EOF
    aws ec2 run-instances --image-id "$ami" --instance-type "$type" --count 1 \
      --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
      --instance-initiated-shutdown-behavior terminate --user-data "file://$ud" \
      --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3,DeleteOnTermination=true}' \
      --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=crestline-bench}]' \
      --query 'Instances[0].InstanceId' --output text
    rm -f "$ud"
  done ;;
ls)
  aws ec2 describe-instances --filters "$TAG" Name=instance-state-name,Values=pending,running,stopping \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,LaunchTime]' --output text ;;
down)
  ids=$(aws ec2 describe-instances --filters "$TAG" Name=instance-state-name,Values=pending,running,stopping \
    --query 'Reservations[].Instances[].InstanceId' --output text)
  [ -n "$ids" ] && aws ec2 terminate-instances --instance-ids $ids --query 'TerminatingInstances[].InstanceId' --output text || echo none ;;
*) sed -n 2,16p "$0"; exit 1 ;;
esac
