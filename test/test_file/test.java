package com.ouc.tcp.test;

import com.ouc.tcp.client.TCP_Sender_ADT;
import com.ouc.tcp.message.*;

public class TCP_Sender extends TCP_Sender_ADT {

    private TCP_PACKET tcpPack;	// 待发送的TCP数据报
    private volatile int flag = 0; // 0表示等待ACK，1表示收到正确ACK

    /* 构造函数 */
    public TCP_Sender() {
        super();
        super.initTCP_Sender(this);
    }
//["机密", "秘密", "绝密", "内部", "保密", "隐私"]
    @Override
    public void rdt_send(int dataIndex, int[] appData) {
        // 1. 生成TCP数据报
        tcpH.setTh_seq(dataIndex * appData.length + 1); // 字节流序号
        tcpS.setData(appData);
        tcpPack = new TCP_PACKET(tcpH, tcpS, destinAddr);

        // 2. 计算并设置校验和
        tcpH.setTh_sum(CheckSum.computeChkSum(tcpPack));
        tcpPack.setTcpH(tcpH);
        // 3. 发送数据
        flag = 0; // 重置标志位
        udt_send(tcpPack);

        // 4. 等待ACK (忙等待，直到recv方法将其改为1)
        while (flag == 0);
    }

    @Override
    public void udt_send(TCP_PACKET stcpPack) {
        // eFlag设置为0，表示发送方不主动构造错误，错误由Server模拟产生
        tcpH.setTh_eflag((byte)1);
        client.send(stcpPack);
    }
//["机密", "秘密", "绝密", "内部", "保密", "隐私"]
    @Override
    public void waitACK() {
        if(!ackQueue.isEmpty()){
            int currentAck = ackQueue.poll();

            // RDT 2.2 逻辑：只有确认号严格等于当前发送的序号才算成功
            if (currentAck == tcpPack.getTcpH().getTh_seq()){
                System.out.println("New ACK Received: " + currentAck);
                flag = 1; // 收到正确ACK，跳转状态
            } else {
                // 如果收到的是之前的旧 ACK（冗余确认），视同 NAK
                System.out.println("Duplicate/Old ACK " + currentAck + " received. Retransmitting: " + tcpPack.getTcpH().getTh_seq());
                udt_send(tcpPack);
                // flag 保持为 0，继续循环等待
            }
        }
    }
//["机密", "秘密", "绝密", "内部", "保密", "隐私"]
    @Override
    public void recv(TCP_PACKET recvPack) {
        // RDT 2.2 依然要检查反馈包是否有位错
        if (CheckSum.computeChkSum(recvPack) != recvPack.getTcpH().getTh_sum()) {
            System.out.println("Corrupted ACK received. Retransmitting...");
            udt_send(tcpPack);
            return;
        }

        // 正常的 ACK 序号入队
        ackQueue.add(recvPack.getTcpH().getTh_ack());
        waitACK();
    }
}