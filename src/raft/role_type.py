from enum import Enum
import rpc.raft_pb2 as raft_pb2
import grpc
from config import *
from random import randrange
import logging
import rpc.raft_pb2_grpc as raft_pb2_grpc
import threading


class RoleType(Enum):
    FOLLOWER = 'follower'
    CANDIDATE = 'candidate'
    LEADER = 'leader'

    def __str__(self):
        return str(self.value)


def dispatch(server):
    switcher = {
        RoleType.FOLLOWER: _Follower,
        RoleType.CANDIDATE: _Candidate,
        RoleType.LEADER: _Leader,
    }
    func = switcher.get(server.role, lambda: "Invalid role")
    return func(server)


class _Role:
    def __init__(self, server):
        self.server = server

    # handle receive
    def vote(self, request, context) -> raft_pb2.RequestVoteReply:
        reply = {'term': self.server.term, 'voteMe': False}
        return raft_pb2.RequestVoteReply(**reply)

    # handle receive
    # TODO:If AppendEntries RPC received from new leader: convert to
    # follower
    def append_entries(self, request, context) -> raft_pb2.AppendEntriesReply:
        pass
        # leader_term = request.term
        # leader_id = request.leaderId
        # prev_log_index = request.prevLogIndex
        # prev_log_term = request.prevLogTerm
        # leader_commit_index = request.leaderCommitIndex
        # success = False
        # if leader_term < self.server.term:
        #     success = False
        # else:
        #     self.server.become(RoleType.FOLLOWER)
        #     if prev_log_index == -1:
        #         success = True
        #         self.server.log = request.entries
        #     elif prev_log_term == self.server.log[prev_log_index].term and len(self.server.log) > prev_log_index:
        #         success = True
        #         self.server.log = self.server.log[:prev_log_index + 1] + request.entries
        # if leader_commit_index > self.server.committed_index:
        #     self.server.commit_index = min(leader_commit_index, len(self.server.log) - 1)
        # if leader_term > self.server.term:
        #     self.server.term = leader_term
        #
        # self.server.reset_timer(self.server.leader_died, HEARTBEAT_INTERVAL_SECONDS)
        # reply = {"term": self.server.term, "success": success}
        # return raft_pb2.AppendEntriesReply(**reply)


class _Follower(_Role):
    def run(self):
        # print('test')f
        self.server.vote_for = -1
        self.server.votes_granted = 0
        self.server.timeout = float(
            randrange(ELECTION_TIMEOUT_MAX_MILLIS // 2, ELECTION_TIMEOUT_MAX_MILLIS) / 1000)
        # self.server.reset_timer(self.server.leader_died, HEARTBEAT_INTERVAL_SECONDS)
        self.server.reset_timer(lambda : print("reachingiiiiiiiii"), self.server.timeout)

    def vote(self, request, context) -> raft_pb2.RequestVoteReply:
        should_vote = False
        candidate_id = request.candidateId
        candidate_term = request.term
        candidate_last_log_index = request.lastLogIndex
        # vote for the candidate with the higher term
        if candidate_term < self.server.term:
            should_vote = False
        else:
            if self.server.vote_for == -1 or self.server.vote_for == candidate_id:
                if candidate_last_log_index >= self.server.get_last_log_index():
                    should_vote = True
                    self.server.vote_for = candidate_id
                    self.server.term = candidate_term
                    # TODO: self.server.become(RoleType.FOLLOWER)

        reply = {'term': self.server.term, 'voteMe': should_vote}
        return raft_pb2.RequestVoteReply(**reply)

    def append_entries(self, request, context) -> raft_pb2.AppendEntriesReply:
        leader_term = request.term
        leader_id = request.leaderId
        prev_log_index = request.prevLogIndex
        prev_log_term = request.prevLogTerm
        leader_commit_index = request.leaderCommitIndex
        success = False
        self.server.reset_timeout()
        if leader_term < self.server.term:
            return raft_pb2.AppendEntriesReply(term=self.server.term, success=False)
        # TODO:


class _Candidate(_Role):
    # TODO: barrier
    def run(self):
        self.server.term += 1
        self.server.votes_granted = 1
        self.server.vote_for = self.server.id
        self.server.reset_timeout()

        # barrier = threading.Barrier(self.server.majority - 1, timeout=self.server.timeout)
        barrier = None
        for value in self.server.peers:
            self.ask_vote(value, barrier)
            # threading.Thread(target=self.ask_vote,args=(value,barrier)).start()
        # self.server.reset_timer(self.process_vote, self.server.timeout)

        self.server.reset_timer(self.process_vote, self.server.timeout)

    # TODO: barrier
    def ask_vote(self, address: str, barrier: threading.Barrier):
        print('ask vote', address)
        try:
            with grpc.insecure_channel(address) as channel:
                stub = raft_pb2_grpc.RaftStub(channel)
                args = {'term': self.server.term,
                        'candidateId': self.server.id,
                        'lastLogIndex': self.server.get_last_log_index(),
                        'lastLogTerm': self.server.get_last_log_term()}
                request = raft_pb2.RequestVoteRequest(**args)
                response = stub.RequestVote(request)
                if response.voteMe:
                    self.server.votes_granted += 1
                if response.term > self.server.term:
                    self.server.term = response.term
                    self.server.become(RoleType.FOLLOWER)
                    self.server.vote_for = -1
                    self.server.votes_granted = 0
                    self.server.timeout = float(
                        randrange(ELECTION_TIMEOUT_MAX_MILLIS // 2, ELECTION_TIMEOUT_MAX_MILLIS) / 1000)
                    self.server.reset_timer(self.server.leader_died, HEARTBEAT_INTERVAL_SECONDS)
        except grpc.RpcError as e:
            print(e)
            logging.error("connection error")

    def process_vote(self):
        print("process vote, votes_granted", self.server.votes_granted)
        if self.server.votes_granted >= self.server.majority:
            # logging.info("become leader")
            self.server.become(RoleType.LEADER)
        else:
            self.server.become(RoleType.CANDIDATE)

    def append_entries(self, request, context) -> raft_pb2.AppendEntriesReply:
        leader_term = request.term
        leader_id = request.leaderId
        prev_log_index = request.prevLogIndex
        prev_log_term = request.prevLogTerm
        leader_commit_index = request.leaderCommitIndex

        if leader_term > self.server.term:
            self.server.become(RoleType.FOLLOWER)
        elif prev_log_term > self.server.get_last_log_term():
            self.server.become(RoleType.FOLLOWER)
        elif prev_log_term == self.server.get_last_log_term() and prev_log_index >= self.server.get_last_log_index():
            self.server.become(RoleType.FOLLOWER)
        return raft_pb2.AppendEntriesReply(term=self.server.term, success=False)


class _Leader(_Role):
    def run(self):
        print("I am leader leading")
        self.server.next_index = {key: len(self.server.log) for key in self.server.peers}
        self.server.match_index = {key: -1 for key in self.server.peers}
        # TODO: heartbeat
        self.server.reset_timer(self.broadcast_append_entries, HEARTBEAT_INTERVAL_SECONDS)

    def broadcast_append_entries(self):
        # TODO: multi-thread
        for value in self.server.peers:
            self.send_append_entries(value)

    def send_append_entries(self, address: str):
        try:
            with grpc.insecure_channel(address) as channel:
                stub = raft_pb2_grpc.RaftStub(channel)
                prev_log_index = self.server.next_index[address] - 1
                entries = self.server.log[self.server.next_index[address]:]
                args = {'term': self.server.term,
                        'leaderId': self.server.id,
                        'prevLogIndex': prev_log_index,
                        'prevLogTerm': self.server.log[prev_log_index].term if prev_log_index != -1 else 0,
                        'entries': entries,
                        'leaderCommitIndex': self.server.committed_index}
                if DEBUG:
                    if len(entries) > 0:
                        logging.debug(self.server.id, "send append entries to nextIndex[i]",
                                      self.server.nextIndex[address],
                                      "with args", args, "to", address)
                    else:
                        logging.debug(str(self.server.id) + " send heartbeat to" + address)
                response = stub.AppendEntries(raft_pb2.AppendEntriesRequest(**args))
                if response.term > self.server.term:

                    print("will become follower, other is in term: ", response.term, "I am in term: ",self.server.term)
                    self.server.term = response.term
                    self.server.become(RoleType.FOLLOWER)
                if not response.success:
                    self.server.next_index[address] -= 1
                else:
                    # TODO
                    self.server.next_index[address] += len(entries)
                    self.server.match_index[address] = self.server.next_index[address] - 1

        except grpc.RpcError as e:
            print(e)
            logging.error("connection error")
