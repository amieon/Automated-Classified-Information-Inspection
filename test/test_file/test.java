/**
 * 2.1: ACK/NACK
 * Feng Hong; 2015-12-09
 */

package com.ouc.tcp.test;

import com.ouc.tcp.client.*;
import com.ouc.tcp.message.*;
import com.ouc.tcp.client.*;
import java.io.*;
import java.time.*;
import java.time.format.*;
import java.util.TimerTask;
import java.util.concurrent.LinkedBlockingDeque;


public class TCP_Sender extends TCP_Sender_ADT {
    PrintWriter CSVwriter = new PrintWriter(new FileWriter("cwnd_ssthresh.csv", false));
    private final LinkedBlockingDeque<TCP_PACKET> window_packet = new LinkedBlockingDeque<>();
    private final LinkedBlockingDeque<TCP_PACKET> memory_packet = new LinkedBlockingDeque<>();
    private static final int MAX_memory_SIZE = 8;

    private UDT_Timer timer = new UDT_Timer();
    private static final int delay_time = 1500;
    private static final int period_time = 1500;

    private int lastAck = -1;
    private int dupAckCount = 0;




    private void resetTimer() {
        class GBN_RetransTask extends TimerTask {
            @Override public void run() {
                try {
                    Timeout();
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            }
        }
        timer.cancel();
        timer = new UDT_Timer();
        if (!window_packet.isEmpty()) {
            timer.schedule(new GBN_RetransTask(), delay_time, period_time);
        }
    }

    void pushTcpPacket(TCP_PACKET packet) {
        memory_packet.offerLast(packet);
        SendPackets();
    }

    void ackPacket(int ack) throws IOException {
        int acked = 0;
        while (true) {
            TCP_PACKET packet = window_packet.peekFirst();

            if (packet != null && packet.getTcpH().getTh_seq() <= ack) {
                window_packet.pollFirst();
                acked += 1;
                resetTimer();
            } else break;
        }

        if (ack == lastAck) {
            dupAckCount += 1;
            if (dupAckCount == 3) {

                ssthresh = Math.max(cwnd / 2, 2);
                cwnd = ssthresh;
                Growth_Index = cwnd;
                state = TcpRenoState.fastRecovery;
                log2Csv();
                fast_retransmit(ack);
            } else if (dupAckCount > 3) {


                state = TcpRenoState.fastRecovery;
                cwnd += 1;
                Growth_Index = cwnd;
                log2Csv();

                SendPackets();
            }
            return;
        }
        lastAck = ack;
        dupAckCount = 1;

            if (state == TcpRenoState.fastRecovery) {
                state = TcpRenoState.congestionAvoid;
                cwnd = ssthresh;
                Growth_Index = cwnd;
                log2Csv();
            }


        if (acked > 0) {
            onAck(acked);

            while (window_packet.size() > cwnd) {
                TCP_PACKET removed = window_packet.pollLast();
                if (removed != null) memory_packet.offerFirst(removed);
            }

            SendPackets();
        }
    }


    private void fast_retransmit(int ack) {
        int expectedsequence = ack + 100; // 与 Receiver 的 ACK 语义一致（最后按序 sequence）
        for (TCP_PACKET packet : window_packet) {
            if (packet.getTcpH().getTh_seq() == expectedsequence) {
                this.udt_send(packet);

                while (window_packet.size() > cwnd) {
                    TCP_PACKET removed = window_packet.pollLast();
                    if (removed != null) memory_packet.offerFirst(removed);
                }

                break;
            }
        }
    }

    void Timeout() throws IOException {
        timer.cancel();

        state = TcpRenoState.slowStart;
        ssthresh = Math.max(cwnd / 2, 2);
        cwnd = 1;
        Growth_Index = 1.0;
        log2Csv();

        while (window_packet.size() > cwnd) {
            TCP_PACKET should_removed = window_packet.pollLast();
            if (should_removed != null) memory_packet.offerFirst(should_removed);
        }


        TCP_PACKET retransmitpacket = window_packet.peekFirst();
        if (retransmitpacket != null) this.udt_send(retransmitpacket);
        resetTimer();
    }

    synchronized void SendPackets() {
        while (!memory_packet.isEmpty() && window_packet.size() < cwnd) {
            TCP_PACKET packet = memory_packet.pollFirst();
            if (packet == null || packet.getTcpH().getTh_seq() <= lastAck) continue;
            this.udt_send(packet);
            window_packet.offerLast(packet);
            if (window_packet.size() == 1)
                resetTimer();
        }
    }
    // 发送者窗口

    /* 构造函数 */
    public TCP_Sender() throws IOException {
        // 调用超类构造函数
        super();
        // 初始化 TCP 发送端
        super.initTCP_Sender(this);

    }

    @Override
    // 可靠发送（应用层调用）：封装应用层数据，产生 TCP 数据报；需要修改
    public void rdt_send(int dataIndex, int[] appData) {
        // 待发送的 TCP 数据报
        TCP_PACKET tcpPack;
        // 生成 TCP 数据报（设置序号和数据字段/校验和),注意打包的顺序
        // 包序号设置为字节流号：
        tcpH.setTh_seq(dataIndex * appData.length + 1);
        tcpS.setData(appData);
        tcpPack = new TCP_PACKET(tcpH, tcpS, destinAddr);
        // 更新带有 checksum 的 TCP 报文头
        tcpH.setTh_sum(CheckSum.computeChkSum(tcpPack));
        tcpPack.setTcpH(tcpH);

        while (memory_packet.size() >= MAX_memory_SIZE) {
            Thread.onSpinWait();
        }

        try {
            pushTcpPacket(tcpPack.clone());
        } catch (CloneNotSupportedException e) {
            e.printStackTrace();
        }
    }

    @Override
    // 不可靠发送：将打包好的 TCP 数据报通过不可靠传输信道发送；仅需修改错误标志
    public void udt_send(TCP_PACKET stcpPack) {
        tcpH.setTh_eflag((byte) 7);
        client.send(stcpPack);
    }

    @Override
    public void waitACK() {

    }

    @Override
    // 接收到 ACK 报文：检查校验和，将确认号插入 ack 队列; NACK 的确认号为 -1；不需要修改
    public void recv(TCP_PACKET recvPack) {
        System.out.println("Receive ACK Number： " + recvPack.getTcpH().getTh_ack());
        ackQueue.add(recvPack.getTcpH().getTh_ack());
        System.out.println();

        // 处理 ACK 报文
        if (!ackQueue.isEmpty()) {
            int curAck = ackQueue.poll();
            try {
                ackPacket(curAck);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }
    }
    private int cwnd = 1;
    private double Growth_Index = 1.0;
    private int ssthresh = 16;

    enum TcpRenoState {
        slowStart, congestionAvoid, fastRetransmit, fastRecovery;
        @Override
        public String toString() {
            return name();
        }
    }

    private TcpRenoState state = TcpRenoState.slowStart;

    private final DateTimeFormatter timeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss.SSS");


    private final Object logLock = new Object();
    private void log2Csv() throws IOException {
        synchronized (logLock) {
            String timestamp = LocalTime.now().format(timeFormatter);
            CSVwriter.printf("%s,%d,%d,%s%n", timestamp, cwnd, ssthresh, state.toString());
            CSVwriter.flush();
        }
    }
//["机密", "秘密", "绝密", "内部", "保密", "隐私"]
    void onAck(int acked) throws IOException {
        if (state == TcpRenoState.slowStart) {

            int newCwnd = Math.min(cwnd + acked, ssthresh);
            int usedAcked = newCwnd - cwnd;
            cwnd = newCwnd;
            Growth_Index = cwnd;
            log2Csv();
            acked = acked - usedAcked;

            if (acked == 0) return;
        }
        state = TcpRenoState.congestionAvoid;

        Growth_Index += (double) acked / cwnd;
        cwnd = (int) Growth_Index;
        log2Csv();
    }

}