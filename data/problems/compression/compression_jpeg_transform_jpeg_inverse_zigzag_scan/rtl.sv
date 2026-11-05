module compression_jpeg_transform_jpeg_inverse_zigzag_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_coeff_0,
    input  logic signed [15:0] in_coeff_1,
    input  logic signed [15:0] in_coeff_2,
    input  logic signed [15:0] in_coeff_3,
    input  logic signed [15:0] in_coeff_4,
    input  logic signed [15:0] in_coeff_5,
    input  logic signed [15:0] in_coeff_6,
    input  logic signed [15:0] in_coeff_7,
    input  logic signed [15:0] in_coeff_8,
    input  logic signed [15:0] in_coeff_9,
    input  logic signed [15:0] in_coeff_10,
    input  logic signed [15:0] in_coeff_11,
    input  logic signed [15:0] in_coeff_12,
    input  logic signed [15:0] in_coeff_13,
    input  logic signed [15:0] in_coeff_14,
    input  logic signed [15:0] in_coeff_15,
    input  logic signed [15:0] in_coeff_16,
    input  logic signed [15:0] in_coeff_17,
    input  logic signed [15:0] in_coeff_18,
    input  logic signed [15:0] in_coeff_19,
    input  logic signed [15:0] in_coeff_20,
    input  logic signed [15:0] in_coeff_21,
    input  logic signed [15:0] in_coeff_22,
    input  logic signed [15:0] in_coeff_23,
    input  logic signed [15:0] in_coeff_24,
    input  logic signed [15:0] in_coeff_25,
    input  logic signed [15:0] in_coeff_26,
    input  logic signed [15:0] in_coeff_27,
    input  logic signed [15:0] in_coeff_28,
    input  logic signed [15:0] in_coeff_29,
    input  logic signed [15:0] in_coeff_30,
    input  logic signed [15:0] in_coeff_31,
    input  logic signed [15:0] in_coeff_32,
    input  logic signed [15:0] in_coeff_33,
    input  logic signed [15:0] in_coeff_34,
    input  logic signed [15:0] in_coeff_35,
    input  logic signed [15:0] in_coeff_36,
    input  logic signed [15:0] in_coeff_37,
    input  logic signed [15:0] in_coeff_38,
    input  logic signed [15:0] in_coeff_39,
    input  logic signed [15:0] in_coeff_40,
    input  logic signed [15:0] in_coeff_41,
    input  logic signed [15:0] in_coeff_42,
    input  logic signed [15:0] in_coeff_43,
    input  logic signed [15:0] in_coeff_44,
    input  logic signed [15:0] in_coeff_45,
    input  logic signed [15:0] in_coeff_46,
    input  logic signed [15:0] in_coeff_47,
    input  logic signed [15:0] in_coeff_48,
    input  logic signed [15:0] in_coeff_49,
    input  logic signed [15:0] in_coeff_50,
    input  logic signed [15:0] in_coeff_51,
    input  logic signed [15:0] in_coeff_52,
    input  logic signed [15:0] in_coeff_53,
    input  logic signed [15:0] in_coeff_54,
    input  logic signed [15:0] in_coeff_55,
    input  logic signed [15:0] in_coeff_56,
    input  logic signed [15:0] in_coeff_57,
    input  logic signed [15:0] in_coeff_58,
    input  logic signed [15:0] in_coeff_59,
    input  logic signed [15:0] in_coeff_60,
    input  logic signed [15:0] in_coeff_61,
    input  logic signed [15:0] in_coeff_62,
    input  logic signed [15:0] in_coeff_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_coeff_0,
    output logic signed [15:0] out_coeff_1,
    output logic signed [15:0] out_coeff_2,
    output logic signed [15:0] out_coeff_3,
    output logic signed [15:0] out_coeff_4,
    output logic signed [15:0] out_coeff_5,
    output logic signed [15:0] out_coeff_6,
    output logic signed [15:0] out_coeff_7,
    output logic signed [15:0] out_coeff_8,
    output logic signed [15:0] out_coeff_9,
    output logic signed [15:0] out_coeff_10,
    output logic signed [15:0] out_coeff_11,
    output logic signed [15:0] out_coeff_12,
    output logic signed [15:0] out_coeff_13,
    output logic signed [15:0] out_coeff_14,
    output logic signed [15:0] out_coeff_15,
    output logic signed [15:0] out_coeff_16,
    output logic signed [15:0] out_coeff_17,
    output logic signed [15:0] out_coeff_18,
    output logic signed [15:0] out_coeff_19,
    output logic signed [15:0] out_coeff_20,
    output logic signed [15:0] out_coeff_21,
    output logic signed [15:0] out_coeff_22,
    output logic signed [15:0] out_coeff_23,
    output logic signed [15:0] out_coeff_24,
    output logic signed [15:0] out_coeff_25,
    output logic signed [15:0] out_coeff_26,
    output logic signed [15:0] out_coeff_27,
    output logic signed [15:0] out_coeff_28,
    output logic signed [15:0] out_coeff_29,
    output logic signed [15:0] out_coeff_30,
    output logic signed [15:0] out_coeff_31,
    output logic signed [15:0] out_coeff_32,
    output logic signed [15:0] out_coeff_33,
    output logic signed [15:0] out_coeff_34,
    output logic signed [15:0] out_coeff_35,
    output logic signed [15:0] out_coeff_36,
    output logic signed [15:0] out_coeff_37,
    output logic signed [15:0] out_coeff_38,
    output logic signed [15:0] out_coeff_39,
    output logic signed [15:0] out_coeff_40,
    output logic signed [15:0] out_coeff_41,
    output logic signed [15:0] out_coeff_42,
    output logic signed [15:0] out_coeff_43,
    output logic signed [15:0] out_coeff_44,
    output logic signed [15:0] out_coeff_45,
    output logic signed [15:0] out_coeff_46,
    output logic signed [15:0] out_coeff_47,
    output logic signed [15:0] out_coeff_48,
    output logic signed [15:0] out_coeff_49,
    output logic signed [15:0] out_coeff_50,
    output logic signed [15:0] out_coeff_51,
    output logic signed [15:0] out_coeff_52,
    output logic signed [15:0] out_coeff_53,
    output logic signed [15:0] out_coeff_54,
    output logic signed [15:0] out_coeff_55,
    output logic signed [15:0] out_coeff_56,
    output logic signed [15:0] out_coeff_57,
    output logic signed [15:0] out_coeff_58,
    output logic signed [15:0] out_coeff_59,
    output logic signed [15:0] out_coeff_60,
    output logic signed [15:0] out_coeff_61,
    output logic signed [15:0] out_coeff_62,
    output logic signed [15:0] out_coeff_63
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
        out_coeff_0  <= '0;
        out_coeff_1  <= '0;
        out_coeff_2  <= '0;
        out_coeff_3  <= '0;
        out_coeff_4  <= '0;
        out_coeff_5  <= '0;
        out_coeff_6  <= '0;
        out_coeff_7  <= '0;
        out_coeff_8  <= '0;
        out_coeff_9  <= '0;
        out_coeff_10 <= '0;
        out_coeff_11 <= '0;
        out_coeff_12 <= '0;
        out_coeff_13 <= '0;
        out_coeff_14 <= '0;
        out_coeff_15 <= '0;
        out_coeff_16 <= '0;
        out_coeff_17 <= '0;
        out_coeff_18 <= '0;
        out_coeff_19 <= '0;
        out_coeff_20 <= '0;
        out_coeff_21 <= '0;
        out_coeff_22 <= '0;
        out_coeff_23 <= '0;
        out_coeff_24 <= '0;
        out_coeff_25 <= '0;
        out_coeff_26 <= '0;
        out_coeff_27 <= '0;
        out_coeff_28 <= '0;
        out_coeff_29 <= '0;
        out_coeff_30 <= '0;
        out_coeff_31 <= '0;
        out_coeff_32 <= '0;
        out_coeff_33 <= '0;
        out_coeff_34 <= '0;
        out_coeff_35 <= '0;
        out_coeff_36 <= '0;
        out_coeff_37 <= '0;
        out_coeff_38 <= '0;
        out_coeff_39 <= '0;
        out_coeff_40 <= '0;
        out_coeff_41 <= '0;
        out_coeff_42 <= '0;
        out_coeff_43 <= '0;
        out_coeff_44 <= '0;
        out_coeff_45 <= '0;
        out_coeff_46 <= '0;
        out_coeff_47 <= '0;
        out_coeff_48 <= '0;
        out_coeff_49 <= '0;
        out_coeff_50 <= '0;
        out_coeff_51 <= '0;
        out_coeff_52 <= '0;
        out_coeff_53 <= '0;
        out_coeff_54 <= '0;
        out_coeff_55 <= '0;
        out_coeff_56 <= '0;
        out_coeff_57 <= '0;
        out_coeff_58 <= '0;
        out_coeff_59 <= '0;
        out_coeff_60 <= '0;
        out_coeff_61 <= '0;
        out_coeff_62 <= '0;
        out_coeff_63 <= '0;
    end else if (input_accepted) begin
        busy <= 1'b1;

        out_coeff_0  <= in_coeff_0;
        out_coeff_1  <= in_coeff_1;
        out_coeff_2  <= in_coeff_5;
        out_coeff_3  <= in_coeff_6;
        out_coeff_4  <= in_coeff_14;
        out_coeff_5  <= in_coeff_15;
        out_coeff_6  <= in_coeff_27;
        out_coeff_7  <= in_coeff_28;
        out_coeff_8  <= in_coeff_2;
        out_coeff_9  <= in_coeff_4;
        out_coeff_10 <= in_coeff_7;
        out_coeff_11 <= in_coeff_13;
        out_coeff_12 <= in_coeff_16;
        out_coeff_13 <= in_coeff_26;
        out_coeff_14 <= in_coeff_29;
        out_coeff_15 <= in_coeff_42;
        out_coeff_16 <= in_coeff_3;
        out_coeff_17 <= in_coeff_8;
        out_coeff_18 <= in_coeff_12;
        out_coeff_19 <= in_coeff_17;
        out_coeff_20 <= in_coeff_25;
        out_coeff_21 <= in_coeff_30;
        out_coeff_22 <= in_coeff_41;
        out_coeff_23 <= in_coeff_43;
        out_coeff_24 <= in_coeff_9;
        out_coeff_25 <= in_coeff_11;
        out_coeff_26 <= in_coeff_18;
        out_coeff_27 <= in_coeff_24;
        out_coeff_28 <= in_coeff_31;
        out_coeff_29 <= in_coeff_40;
        out_coeff_30 <= in_coeff_44;
        out_coeff_31 <= in_coeff_53;
        out_coeff_32 <= in_coeff_10;
        out_coeff_33 <= in_coeff_19;
        out_coeff_34 <= in_coeff_23;
        out_coeff_35 <= in_coeff_32;
        out_coeff_36 <= in_coeff_39;
        out_coeff_37 <= in_coeff_45;
        out_coeff_38 <= in_coeff_52;
        out_coeff_39 <= in_coeff_54;
        out_coeff_40 <= in_coeff_20;
        out_coeff_41 <= in_coeff_22;
        out_coeff_42 <= in_coeff_33;
        out_coeff_43 <= in_coeff_38;
        out_coeff_44 <= in_coeff_46;
        out_coeff_45 <= in_coeff_51;
        out_coeff_46 <= in_coeff_55;
        out_coeff_47 <= in_coeff_60;
        out_coeff_48 <= in_coeff_21;
        out_coeff_49 <= in_coeff_34;
        out_coeff_50 <= in_coeff_37;
        out_coeff_51 <= in_coeff_47;
        out_coeff_52 <= in_coeff_50;
        out_coeff_53 <= in_coeff_56;
        out_coeff_54 <= in_coeff_59;
        out_coeff_55 <= in_coeff_61;
        out_coeff_56 <= in_coeff_35;
        out_coeff_57 <= in_coeff_36;
        out_coeff_58 <= in_coeff_48;
        out_coeff_59 <= in_coeff_49;
        out_coeff_60 <= in_coeff_57;
        out_coeff_61 <= in_coeff_58;
        out_coeff_62 <= in_coeff_62;
        out_coeff_63 <= in_coeff_63;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule