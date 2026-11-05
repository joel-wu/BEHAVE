module packet_controller (
    input logic clk, rst, rx_valid_i,
    input logic [7:0] rx_data_8_i,
    input logic tx_done_tick_i,
    output logic tx_start_o,
    output logic [7:0] tx_data_8_o
);
    typedef enum logic [2:0] {S_IDLE, S_GOT_8_BYTES, S_RECV_CHECKSUM,
        S_BUILD_RESPONSE, S_SEND_FIRST_BYTE, S_RESPONSE_READY} state_t;
    state_t state;
    logic [2:0] count, sent;
    logic [63:0] packet;
    logic [7:0] checksum, packet_sum, response_check;
    logic [39:0] response;
    logic [15:0] result;
    always_comb begin
        packet_sum=0;
        for(int i=0;i<8;i++) packet_sum=packet_sum+packet[8*i+:8];
        case(packet[15:8])
            8'h00: result=packet[47:32]+packet[31:16];
            8'h01: result=packet[47:32]-packet[31:16];
            default: result=0;
        endcase
        response_check=8'h00-8'hAB-8'hCD-result[15:8]-result[7:0];
    end
    always_ff @(posedge clk or posedge rst) begin
        if(rst) begin
            state<=S_IDLE;count<=0;sent<=0;packet<=0;checksum<=0;
            response<=0;tx_start_o<=0;tx_data_8_o<=0;
        end else case(state)
            S_IDLE: if(rx_valid_i) begin
                packet<={packet[55:0],rx_data_8_i};
                if(count==7) begin count<=0;state<=S_GOT_8_BYTES;end
                else count<=count+1'b1;
            end
            S_GOT_8_BYTES: begin
                checksum<=packet_sum;
                state<=(packet[63:48]==16'hBACD) ? S_RECV_CHECKSUM : S_IDLE;
            end
            S_RECV_CHECKSUM: state<=(checksum==0) ? S_BUILD_RESPONSE : S_IDLE;
            S_BUILD_RESPONSE: begin
                response<={16'hABCD,result,response_check};state<=S_SEND_FIRST_BYTE;
            end
            S_SEND_FIRST_BYTE: begin
                tx_start_o<=1;tx_data_8_o<=response[39:32];sent<=0;state<=S_RESPONSE_READY;
            end
            S_RESPONSE_READY: if(tx_done_tick_i) begin
                if(sent==4) begin
                    state<=S_IDLE;tx_start_o<=0;tx_data_8_o<=0;response<=0;
                end else begin
                    response<={response[31:0],8'h00};tx_data_8_o<=response[31:24];sent<=sent+1'b1;
                end
            end
            default: begin state<=S_IDLE;count<=0;tx_start_o<=0;tx_data_8_o<=0;end
        endcase
    end
endmodule
