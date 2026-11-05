module fec_rep5 (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [1:0] in_mode,
    input  logic [7:0] in_data0,
    input  logic [7:0] in_data1,
    input  logic [7:0] in_data2,
    input  logic [7:0] in_data3,
    input  logic [7:0] in_data4,
    input  logic [7:0] in_data5,
    input  logic [7:0] in_data6,
    input  logic [7:0] in_data7,
    input  logic [7:0] in_data8,
    input  logic [7:0] in_data9,
    input  logic [7:0] in_data10,
    input  logic [7:0] in_data11,
    input  logic [7:0] in_data12,
    input  logic [7:0] in_data13,
    input  logic [7:0] in_data14,
    input  logic [7:0] in_data15,
    input  logic [7:0] in_data16,
    input  logic [7:0] in_data17,
    input  logic [7:0] in_data18,
    input  logic [7:0] in_data19,
    input  logic [7:0] in_data20,
    input  logic [7:0] in_data21,
    input  logic [7:0] in_data22,
    input  logic [7:0] in_data23,
    input  logic [7:0] in_data24,
    input  logic [7:0] in_data25,
    input  logic [7:0] in_data26,
    input  logic [7:0] in_data27,
    input  logic [7:0] in_data28,
    input  logic [7:0] in_data29,
    input  logic [7:0] in_data30,
    input  logic [7:0] in_data31,
    input  logic [7:0] in_data32,
    input  logic [7:0] in_data33,
    input  logic [7:0] in_data34,
    input  logic [7:0] in_data35,
    input  logic [7:0] in_data36,
    input  logic [7:0] in_data37,
    input  logic [7:0] in_data38,
    input  logic [7:0] in_data39,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);

    logic       busy;
    logic [2:0] beats_sent;
    logic [7:0] out_data_reg;
    logic [7:0] encode_byte;
    logic       frame_is_encode;

    logic [7:0] hard_decoded;
    logic [7:0] soft_decoded;

    logic [10:0] sum0;
    logic [10:0] sum1;
    logic [10:0] sum2;
    logic [10:0] sum3;
    logic [10:0] sum4;
    logic [10:0] sum5;
    logic [10:0] sum6;
    logic [10:0] sum7;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign out_valid = busy;
    assign in_ready = (~busy) | frame_complete;
    assign out_data = out_valid ? out_data_reg : 8'h00;

    assign input_accepted = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete = output_consumed & ((frame_is_encode == 1'b0) | (beats_sent == 3'd4));

    always_comb begin
        hard_decoded =
            (in_data0 & in_data1 & in_data2) |
            (in_data0 & in_data1 & in_data3) |
            (in_data0 & in_data1 & in_data4) |
            (in_data0 & in_data2 & in_data3) |
            (in_data0 & in_data2 & in_data4) |
            (in_data0 & in_data3 & in_data4) |
            (in_data1 & in_data2 & in_data3) |
            (in_data1 & in_data2 & in_data4) |
            (in_data1 & in_data3 & in_data4) |
            (in_data2 & in_data3 & in_data4);

        sum0 = {3'b000, in_data0}  + {3'b000, in_data8}  + {3'b000, in_data16} + {3'b000, in_data24} + {3'b000, in_data32};
        sum1 = {3'b000, in_data1}  + {3'b000, in_data9}  + {3'b000, in_data17} + {3'b000, in_data25} + {3'b000, in_data33};
        sum2 = {3'b000, in_data2}  + {3'b000, in_data10} + {3'b000, in_data18} + {3'b000, in_data26} + {3'b000, in_data34};
        sum3 = {3'b000, in_data3}  + {3'b000, in_data11} + {3'b000, in_data19} + {3'b000, in_data27} + {3'b000, in_data35};
        sum4 = {3'b000, in_data4}  + {3'b000, in_data12} + {3'b000, in_data20} + {3'b000, in_data28} + {3'b000, in_data36};
        sum5 = {3'b000, in_data5}  + {3'b000, in_data13} + {3'b000, in_data21} + {3'b000, in_data29} + {3'b000, in_data37};
        sum6 = {3'b000, in_data6}  + {3'b000, in_data14} + {3'b000, in_data22} + {3'b000, in_data30} + {3'b000, in_data38};
        sum7 = {3'b000, in_data7}  + {3'b000, in_data15} + {3'b000, in_data23} + {3'b000, in_data31} + {3'b000, in_data39};

        soft_decoded = 8'h00;
        if (sum0 >= 11'd640) soft_decoded[7] = 1'b1;
        if (sum1 >= 11'd640) soft_decoded[6] = 1'b1;
        if (sum2 >= 11'd640) soft_decoded[5] = 1'b1;
        if (sum3 >= 11'd640) soft_decoded[4] = 1'b1;
        if (sum4 >= 11'd640) soft_decoded[3] = 1'b1;
        if (sum5 >= 11'd640) soft_decoded[2] = 1'b1;
        if (sum6 >= 11'd640) soft_decoded[1] = 1'b1;
        if (sum7 >= 11'd640) soft_decoded[0] = 1'b1;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy            <= 1'b0;
            beats_sent      <= 3'd0;
            out_data_reg    <= 8'h00;
            encode_byte     <= 8'h00;
            frame_is_encode <= 1'b0;
        end else if (input_accepted) begin
            if (in_mode == 2'b01) begin
                busy            <= 1'b1;
                beats_sent      <= 3'd0;
                out_data_reg    <= hard_decoded;
                encode_byte     <= 8'h00;
                frame_is_encode <= 1'b0;
            end else if (in_mode == 2'b10) begin
                busy            <= 1'b1;
                beats_sent      <= 3'd0;
                out_data_reg    <= soft_decoded;
                encode_byte     <= 8'h00;
                frame_is_encode <= 1'b0;
            end else begin
                busy            <= 1'b1;
                beats_sent      <= 3'd0;
                out_data_reg    <= in_data0;
                encode_byte     <= in_data0;
                frame_is_encode <= 1'b1;
            end
        end else if (output_consumed) begin
            if (frame_is_encode) begin
                if (beats_sent == 3'd4) begin
                    busy            <= 1'b0;
                    beats_sent      <= 3'd0;
                    out_data_reg    <= out_data_reg;
                    encode_byte     <= encode_byte;
                    frame_is_encode <= frame_is_encode;
                end else begin
                    busy            <= 1'b1;
                    beats_sent      <= beats_sent + 3'd1;
                    out_data_reg    <= encode_byte;
                    encode_byte     <= encode_byte;
                    frame_is_encode <= frame_is_encode;
                end
            end else begin
                busy            <= 1'b0;
                beats_sent      <= 3'd0;
                out_data_reg    <= out_data_reg;
                encode_byte     <= encode_byte;
                frame_is_encode <= frame_is_encode;
            end
        end
    end

endmodule