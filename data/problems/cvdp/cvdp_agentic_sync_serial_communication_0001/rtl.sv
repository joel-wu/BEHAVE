module sync_serial_communication_tx_rx (
    input logic clk, reset_n,
    input logic [63:0] data_in,
    input logic [2:0] sel,
    output logic [63:0] data_out,
    output logic done,
    output logic [63:0] gray_out
);
    wire serial_out,serial_clk,last_bit,complete_toggle;
    logic observed_toggle;
    tx_block #(.REPEAT(1)) tx (
        .clk(clk),.reset_n(reset_n),.data_in(data_in),.sel(sel),
        .serial_out(serial_out),.serial_clk(serial_clk),.last_bit(last_bit));
    rx_block rx (.serial_clk(serial_clk),.reset_n(reset_n),.serial_in(serial_out),
        .last_bit(last_bit),.data_out(data_out),.complete_toggle(complete_toggle));
    always_ff @(posedge clk or negedge reset_n)begin
        if(!reset_n)observed_toggle<=0;
        else observed_toggle<=complete_toggle;
    end
    assign done=complete_toggle ^ observed_toggle;
    binary_to_gray_conversion gray (.data(data_out),.gray_out(gray_out));
endmodule

module binary_to_gray_conversion (
    input wire [63:0] data,output wire [63:0] gray_out
);
    assign gray_out=data ^ (data>>1);
endmodule

module tx_block #(parameter bit REPEAT=0) (
    input logic clk,reset_n,
    input logic [63:0] data_in,
    input logic [2:0] sel,
    output logic serial_out,last_bit,
    output wire serial_clk
);
    logic [63:0] shift_q;
    logic [6:0] remaining,width;
    logic completed,gate_q;
    always_comb begin
        case(sel)
            1:width=8;2:width=16;3:width=32;4:width=64;
            default:width=0;
        endcase
    end
    assign serial_clk=clk & gate_q;
    always_ff @(negedge clk or negedge reset_n) begin
        if(!reset_n)begin gate_q<=0;serial_out<=0;last_bit<=0;end
        else begin
            gate_q<=(remaining!=0);
            serial_out<=(remaining!=0) ? shift_q[0] : 1'b0;
            last_bit<=(remaining==1);
        end
    end
    always_ff @(posedge clk or negedge reset_n) begin
        if(!reset_n)begin shift_q<=0;remaining<=0;completed<=0;end
        else if(remaining!=0)begin
            shift_q<=shift_q>>1;remaining<=remaining-1'b1;
            if(remaining==1)completed<=1;
        end else if((REPEAT || !completed) && width!=0)begin
            shift_q<=data_in;remaining<=width;
        end
    end
endmodule

module rx_block (
    input logic serial_clk,reset_n,serial_in,last_bit,
    output logic [63:0] data_out,
    output logic complete_toggle
);
    logic [63:0] partial;
    logic [5:0] position;
    wire [63:0] next_partial=(position==0 ? 64'b0 : partial) |
                                  (64'(serial_in)<<position);
    always_ff @(posedge serial_clk or negedge reset_n)begin
        if(!reset_n)begin partial<=0;position<=0;data_out<=0;complete_toggle<=0;end
        else begin
            partial<=next_partial;
            if(last_bit)begin
                data_out<=next_partial;position<=0;complete_toggle<=~complete_toggle;
            end else position<=position+1'b1;
        end
    end
endmodule
