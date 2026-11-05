module compression_jpeg_transform_jpeg_zigzag_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coeff_0,
    input  logic [15:0]  in_coeff_1,
    input  logic [15:0]  in_coeff_2,
    input  logic [15:0]  in_coeff_3,
    input  logic [15:0]  in_coeff_4,
    input  logic [15:0]  in_coeff_5,
    input  logic [15:0]  in_coeff_6,
    input  logic [15:0]  in_coeff_7,
    input  logic [15:0]  in_coeff_8,
    input  logic [15:0]  in_coeff_9,
    input  logic [15:0]  in_coeff_10,
    input  logic [15:0]  in_coeff_11,
    input  logic [15:0]  in_coeff_12,
    input  logic [15:0]  in_coeff_13,
    input  logic [15:0]  in_coeff_14,
    input  logic [15:0]  in_coeff_15,
    input  logic [15:0]  in_coeff_16,
    input  logic [15:0]  in_coeff_17,
    input  logic [15:0]  in_coeff_18,
    input  logic [15:0]  in_coeff_19,
    input  logic [15:0]  in_coeff_20,
    input  logic [15:0]  in_coeff_21,
    input  logic [15:0]  in_coeff_22,
    input  logic [15:0]  in_coeff_23,
    input  logic [15:0]  in_coeff_24,
    input  logic [15:0]  in_coeff_25,
    input  logic [15:0]  in_coeff_26,
    input  logic [15:0]  in_coeff_27,
    input  logic [15:0]  in_coeff_28,
    input  logic [15:0]  in_coeff_29,
    input  logic [15:0]  in_coeff_30,
    input  logic [15:0]  in_coeff_31,
    input  logic [15:0]  in_coeff_32,
    input  logic [15:0]  in_coeff_33,
    input  logic [15:0]  in_coeff_34,
    input  logic [15:0]  in_coeff_35,
    input  logic [15:0]  in_coeff_36,
    input  logic [15:0]  in_coeff_37,
    input  logic [15:0]  in_coeff_38,
    input  logic [15:0]  in_coeff_39,
    input  logic [15:0]  in_coeff_40,
    input  logic [15:0]  in_coeff_41,
    input  logic [15:0]  in_coeff_42,
    input  logic [15:0]  in_coeff_43,
    input  logic [15:0]  in_coeff_44,
    input  logic [15:0]  in_coeff_45,
    input  logic [15:0]  in_coeff_46,
    input  logic [15:0]  in_coeff_47,
    input  logic [15:0]  in_coeff_48,
    input  logic [15:0]  in_coeff_49,
    input  logic [15:0]  in_coeff_50,
    input  logic [15:0]  in_coeff_51,
    input  logic [15:0]  in_coeff_52,
    input  logic [15:0]  in_coeff_53,
    input  logic [15:0]  in_coeff_54,
    input  logic [15:0]  in_coeff_55,
    input  logic [15:0]  in_coeff_56,
    input  logic [15:0]  in_coeff_57,
    input  logic [15:0]  in_coeff_58,
    input  logic [15:0]  in_coeff_59,
    input  logic [15:0]  in_coeff_60,
    input  logic [15:0]  in_coeff_61,
    input  logic [15:0]  in_coeff_62,
    input  logic [15:0]  in_coeff_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_coeff_0,
    output logic [15:0]  out_coeff_1,
    output logic [15:0]  out_coeff_2,
    output logic [15:0]  out_coeff_3,
    output logic [15:0]  out_coeff_4,
    output logic [15:0]  out_coeff_5,
    output logic [15:0]  out_coeff_6,
    output logic [15:0]  out_coeff_7,
    output logic [15:0]  out_coeff_8,
    output logic [15:0]  out_coeff_9,
    output logic [15:0]  out_coeff_10,
    output logic [15:0]  out_coeff_11,
    output logic [15:0]  out_coeff_12,
    output logic [15:0]  out_coeff_13,
    output logic [15:0]  out_coeff_14,
    output logic [15:0]  out_coeff_15,
    output logic [15:0]  out_coeff_16,
    output logic [15:0]  out_coeff_17,
    output logic [15:0]  out_coeff_18,
    output logic [15:0]  out_coeff_19,
    output logic [15:0]  out_coeff_20,
    output logic [15:0]  out_coeff_21,
    output logic [15:0]  out_coeff_22,
    output logic [15:0]  out_coeff_23,
    output logic [15:0]  out_coeff_24,
    output logic [15:0]  out_coeff_25,
    output logic [15:0]  out_coeff_26,
    output logic [15:0]  out_coeff_27,
    output logic [15:0]  out_coeff_28,
    output logic [15:0]  out_coeff_29,
    output logic [15:0]  out_coeff_30,
    output logic [15:0]  out_coeff_31,
    output logic [15:0]  out_coeff_32,
    output logic [15:0]  out_coeff_33,
    output logic [15:0]  out_coeff_34,
    output logic [15:0]  out_coeff_35,
    output logic [15:0]  out_coeff_36,
    output logic [15:0]  out_coeff_37,
    output logic [15:0]  out_coeff_38,
    output logic [15:0]  out_coeff_39,
    output logic [15:0]  out_coeff_40,
    output logic [15:0]  out_coeff_41,
    output logic [15:0]  out_coeff_42,
    output logic [15:0]  out_coeff_43,
    output logic [15:0]  out_coeff_44,
    output logic [15:0]  out_coeff_45,
    output logic [15:0]  out_coeff_46,
    output logic [15:0]  out_coeff_47,
    output logic [15:0]  out_coeff_48,
    output logic [15:0]  out_coeff_49,
    output logic [15:0]  out_coeff_50,
    output logic [15:0]  out_coeff_51,
    output logic [15:0]  out_coeff_52,
    output logic [15:0]  out_coeff_53,
    output logic [15:0]  out_coeff_54,
    output logic [15:0]  out_coeff_55,
    output logic [15:0]  out_coeff_56,
    output logic [15:0]  out_coeff_57,
    output logic [15:0]  out_coeff_58,
    output logic [15:0]  out_coeff_59,
    output logic [15:0]  out_coeff_60,
    output logic [15:0]  out_coeff_61,
    output logic [15:0]  out_coeff_62,
    output logic [15:0]  out_coeff_63
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;

        out_coeff_0  <= 16'h0000;
        out_coeff_1  <= 16'h0000;
        out_coeff_2  <= 16'h0000;
        out_coeff_3  <= 16'h0000;
        out_coeff_4  <= 16'h0000;
        out_coeff_5  <= 16'h0000;
        out_coeff_6  <= 16'h0000;
        out_coeff_7  <= 16'h0000;
        out_coeff_8  <= 16'h0000;
        out_coeff_9  <= 16'h0000;
        out_coeff_10 <= 16'h0000;
        out_coeff_11 <= 16'h0000;
        out_coeff_12 <= 16'h0000;
        out_coeff_13 <= 16'h0000;
        out_coeff_14 <= 16'h0000;
        out_coeff_15 <= 16'h0000;
        out_coeff_16 <= 16'h0000;
        out_coeff_17 <= 16'h0000;
        out_coeff_18 <= 16'h0000;
        out_coeff_19 <= 16'h0000;
        out_coeff_20 <= 16'h0000;
        out_coeff_21 <= 16'h0000;
        out_coeff_22 <= 16'h0000;
        out_coeff_23 <= 16'h0000;
        out_coeff_24 <= 16'h0000;
        out_coeff_25 <= 16'h0000;
        out_coeff_26 <= 16'h0000;
        out_coeff_27 <= 16'h0000;
        out_coeff_28 <= 16'h0000;
        out_coeff_29 <= 16'h0000;
        out_coeff_30 <= 16'h0000;
        out_coeff_31 <= 16'h0000;
        out_coeff_32 <= 16'h0000;
        out_coeff_33 <= 16'h0000;
        out_coeff_34 <= 16'h0000;
        out_coeff_35 <= 16'h0000;
        out_coeff_36 <= 16'h0000;
        out_coeff_37 <= 16'h0000;
        out_coeff_38 <= 16'h0000;
        out_coeff_39 <= 16'h0000;
        out_coeff_40 <= 16'h0000;
        out_coeff_41 <= 16'h0000;
        out_coeff_42 <= 16'h0000;
        out_coeff_43 <= 16'h0000;
        out_coeff_44 <= 16'h0000;
        out_coeff_45 <= 16'h0000;
        out_coeff_46 <= 16'h0000;
        out_coeff_47 <= 16'h0000;
        out_coeff_48 <= 16'h0000;
        out_coeff_49 <= 16'h0000;
        out_coeff_50 <= 16'h0000;
        out_coeff_51 <= 16'h0000;
        out_coeff_52 <= 16'h0000;
        out_coeff_53 <= 16'h0000;
        out_coeff_54 <= 16'h0000;
        out_coeff_55 <= 16'h0000;
        out_coeff_56 <= 16'h0000;
        out_coeff_57 <= 16'h0000;
        out_coeff_58 <= 16'h0000;
        out_coeff_59 <= 16'h0000;
        out_coeff_60 <= 16'h0000;
        out_coeff_61 <= 16'h0000;
        out_coeff_62 <= 16'h0000;
        out_coeff_63 <= 16'h0000;
    end else if (input_accepted) begin
        busy <= 1'b1;

        out_coeff_0  <= in_coeff_0;
        out_coeff_1  <= in_coeff_1;
        out_coeff_2  <= in_coeff_8;
        out_coeff_3  <= in_coeff_16;
        out_coeff_4  <= in_coeff_9;
        out_coeff_5  <= in_coeff_2;
        out_coeff_6  <= in_coeff_3;
        out_coeff_7  <= in_coeff_10;
        out_coeff_8  <= in_coeff_17;
        out_coeff_9  <= in_coeff_24;
        out_coeff_10 <= in_coeff_32;
        out_coeff_11 <= in_coeff_25;
        out_coeff_12 <= in_coeff_18;
        out_coeff_13 <= in_coeff_11;
        out_coeff_14 <= in_coeff_4;
        out_coeff_15 <= in_coeff_5;
        out_coeff_16 <= in_coeff_12;
        out_coeff_17 <= in_coeff_19;
        out_coeff_18 <= in_coeff_26;
        out_coeff_19 <= in_coeff_33;
        out_coeff_20 <= in_coeff_40;
        out_coeff_21 <= in_coeff_48;
        out_coeff_22 <= in_coeff_41;
        out_coeff_23 <= in_coeff_34;
        out_coeff_24 <= in_coeff_27;
        out_coeff_25 <= in_coeff_20;
        out_coeff_26 <= in_coeff_13;
        out_coeff_27 <= in_coeff_6;
        out_coeff_28 <= in_coeff_7;
        out_coeff_29 <= in_coeff_14;
        out_coeff_30 <= in_coeff_21;
        out_coeff_31 <= in_coeff_28;
        out_coeff_32 <= in_coeff_35;
        out_coeff_33 <= in_coeff_42;
        out_coeff_34 <= in_coeff_49;
        out_coeff_35 <= in_coeff_56;
        out_coeff_36 <= in_coeff_57;
        out_coeff_37 <= in_coeff_50;
        out_coeff_38 <= in_coeff_43;
        out_coeff_39 <= in_coeff_36;
        out_coeff_40 <= in_coeff_29;
        out_coeff_41 <= in_coeff_22;
        out_coeff_42 <= in_coeff_15;
        out_coeff_43 <= in_coeff_23;
        out_coeff_44 <= in_coeff_30;
        out_coeff_45 <= in_coeff_37;
        out_coeff_46 <= in_coeff_44;
        out_coeff_47 <= in_coeff_51;
        out_coeff_48 <= in_coeff_58;
        out_coeff_49 <= in_coeff_59;
        out_coeff_50 <= in_coeff_52;
        out_coeff_51 <= in_coeff_45;
        out_coeff_52 <= in_coeff_38;
        out_coeff_53 <= in_coeff_31;
        out_coeff_54 <= in_coeff_39;
        out_coeff_55 <= in_coeff_46;
        out_coeff_56 <= in_coeff_53;
        out_coeff_57 <= in_coeff_60;
        out_coeff_58 <= in_coeff_61;
        out_coeff_59 <= in_coeff_54;
        out_coeff_60 <= in_coeff_47;
        out_coeff_61 <= in_coeff_55;
        out_coeff_62 <= in_coeff_62;
        out_coeff_63 <= in_coeff_63;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule