module signal_processing_comm_fec_fec_rep3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_mode,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,
    input  logic [7:0]  in_data2,
    input  logic [7:0]  in_data3,
    input  logic [7:0]  in_data4,
    input  logic [7:0]  in_data5,
    input  logic [7:0]  in_data6,
    input  logic [7:0]  in_data7,
    input  logic [7:0]  in_data8,
    input  logic [7:0]  in_data9,
    input  logic [7:0]  in_data10,
    input  logic [7:0]  in_data11,
    input  logic [7:0]  in_data12,
    input  logic [7:0]  in_data13,
    input  logic [7:0]  in_data14,
    input  logic [7:0]  in_data15,
    input  logic [7:0]  in_data16,
    input  logic [7:0]  in_data17,
    input  logic [7:0]  in_data18,
    input  logic [7:0]  in_data19,
    input  logic [7:0]  in_data20,
    input  logic [7:0]  in_data21,
    input  logic [7:0]  in_data22,
    input  logic [7:0]  in_data23,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data0,
    output logic [7:0]  out_data1,
    output logic [7:0]  out_data2
);

    logic busy;

    logic [9:0] sum0;
    logic [9:0] sum1;
    logic [9:0] sum2;
    logic [9:0] sum3;
    logic [9:0] sum4;
    logic [9:0] sum5;
    logic [9:0] sum6;
    logic [9:0] sum7;

    logic [7:0] calc_out0;
    logic [7:0] calc_out1;
    logic [7:0] calc_out2;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        sum0 = 10'(in_data0)  + 10'(in_data8)  + 10'(in_data16);
        sum1 = 10'(in_data1)  + 10'(in_data9)  + 10'(in_data17);
        sum2 = 10'(in_data2)  + 10'(in_data10) + 10'(in_data18);
        sum3 = 10'(in_data3)  + 10'(in_data11) + 10'(in_data19);
        sum4 = 10'(in_data4)  + 10'(in_data12) + 10'(in_data20);
        sum5 = 10'(in_data5)  + 10'(in_data13) + 10'(in_data21);
        sum6 = 10'(in_data6)  + 10'(in_data14) + 10'(in_data22);
        sum7 = 10'(in_data7)  + 10'(in_data15) + 10'(in_data23);

        calc_out0 = 8'h00;
        calc_out1 = 8'h00;
        calc_out2 = 8'h00;

        unique case (in_mode)
            2'b00, 2'b11: begin
                calc_out0 = in_data0;
                calc_out1 = in_data0;
                calc_out2 = in_data0;
            end
            2'b01: begin
                calc_out0 = (in_data0 & in_data1) | (in_data0 & in_data2) | (in_data1 & in_data2);
                calc_out1 = 8'h00;
                calc_out2 = 8'h00;
            end
            2'b10: begin
                calc_out0[7] = ((sum0 / 10'd3) > 10'd127);
                calc_out0[6] = ((sum1 / 10'd3) > 10'd127);
                calc_out0[5] = ((sum2 / 10'd3) > 10'd127);
                calc_out0[4] = ((sum3 / 10'd3) > 10'd127);
                calc_out0[3] = ((sum4 / 10'd3) > 10'd127);
                calc_out0[2] = ((sum5 / 10'd3) > 10'd127);
                calc_out0[1] = ((sum6 / 10'd3) > 10'd127);
                calc_out0[0] = ((sum7 / 10'd3) > 10'd127);
                calc_out1 = 8'h00;
                calc_out2 = 8'h00;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_data0 <= 8'h00;
            out_data1 <= 8'h00;
            out_data2 <= 8'h00;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_data0 <= calc_out0;
            out_data1 <= calc_out1;
            out_data2 <= calc_out2;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule