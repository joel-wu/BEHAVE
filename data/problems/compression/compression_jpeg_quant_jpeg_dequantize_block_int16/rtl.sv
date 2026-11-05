module compression_jpeg_quant_jpeg_dequantize_block_int16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coef_0,
    input  logic [15:0]  in_coef_1,
    input  logic [15:0]  in_coef_2,
    input  logic [15:0]  in_coef_3,
    input  logic [15:0]  in_coef_4,
    input  logic [15:0]  in_coef_5,
    input  logic [15:0]  in_coef_6,
    input  logic [15:0]  in_coef_7,
    input  logic [15:0]  in_coef_8,
    input  logic [15:0]  in_coef_9,
    input  logic [15:0]  in_coef_10,
    input  logic [15:0]  in_coef_11,
    input  logic [15:0]  in_coef_12,
    input  logic [15:0]  in_coef_13,
    input  logic [15:0]  in_coef_14,
    input  logic [15:0]  in_coef_15,
    input  logic [15:0]  in_coef_16,
    input  logic [15:0]  in_coef_17,
    input  logic [15:0]  in_coef_18,
    input  logic [15:0]  in_coef_19,
    input  logic [15:0]  in_coef_20,
    input  logic [15:0]  in_coef_21,
    input  logic [15:0]  in_coef_22,
    input  logic [15:0]  in_coef_23,
    input  logic [15:0]  in_coef_24,
    input  logic [15:0]  in_coef_25,
    input  logic [15:0]  in_coef_26,
    input  logic [15:0]  in_coef_27,
    input  logic [15:0]  in_coef_28,
    input  logic [15:0]  in_coef_29,
    input  logic [15:0]  in_coef_30,
    input  logic [15:0]  in_coef_31,
    input  logic [15:0]  in_coef_32,
    input  logic [15:0]  in_coef_33,
    input  logic [15:0]  in_coef_34,
    input  logic [15:0]  in_coef_35,
    input  logic [15:0]  in_coef_36,
    input  logic [15:0]  in_coef_37,
    input  logic [15:0]  in_coef_38,
    input  logic [15:0]  in_coef_39,
    input  logic [15:0]  in_coef_40,
    input  logic [15:0]  in_coef_41,
    input  logic [15:0]  in_coef_42,
    input  logic [15:0]  in_coef_43,
    input  logic [15:0]  in_coef_44,
    input  logic [15:0]  in_coef_45,
    input  logic [15:0]  in_coef_46,
    input  logic [15:0]  in_coef_47,
    input  logic [15:0]  in_coef_48,
    input  logic [15:0]  in_coef_49,
    input  logic [15:0]  in_coef_50,
    input  logic [15:0]  in_coef_51,
    input  logic [15:0]  in_coef_52,
    input  logic [15:0]  in_coef_53,
    input  logic [15:0]  in_coef_54,
    input  logic [15:0]  in_coef_55,
    input  logic [15:0]  in_coef_56,
    input  logic [15:0]  in_coef_57,
    input  logic [15:0]  in_coef_58,
    input  logic [15:0]  in_coef_59,
    input  logic [15:0]  in_coef_60,
    input  logic [15:0]  in_coef_61,
    input  logic [15:0]  in_coef_62,
    input  logic [15:0]  in_coef_63,
    input  logic [15:0]  in_qtbl_0,
    input  logic [15:0]  in_qtbl_1,
    input  logic [15:0]  in_qtbl_2,
    input  logic [15:0]  in_qtbl_3,
    input  logic [15:0]  in_qtbl_4,
    input  logic [15:0]  in_qtbl_5,
    input  logic [15:0]  in_qtbl_6,
    input  logic [15:0]  in_qtbl_7,
    input  logic [15:0]  in_qtbl_8,
    input  logic [15:0]  in_qtbl_9,
    input  logic [15:0]  in_qtbl_10,
    input  logic [15:0]  in_qtbl_11,
    input  logic [15:0]  in_qtbl_12,
    input  logic [15:0]  in_qtbl_13,
    input  logic [15:0]  in_qtbl_14,
    input  logic [15:0]  in_qtbl_15,
    input  logic [15:0]  in_qtbl_16,
    input  logic [15:0]  in_qtbl_17,
    input  logic [15:0]  in_qtbl_18,
    input  logic [15:0]  in_qtbl_19,
    input  logic [15:0]  in_qtbl_20,
    input  logic [15:0]  in_qtbl_21,
    input  logic [15:0]  in_qtbl_22,
    input  logic [15:0]  in_qtbl_23,
    input  logic [15:0]  in_qtbl_24,
    input  logic [15:0]  in_qtbl_25,
    input  logic [15:0]  in_qtbl_26,
    input  logic [15:0]  in_qtbl_27,
    input  logic [15:0]  in_qtbl_28,
    input  logic [15:0]  in_qtbl_29,
    input  logic [15:0]  in_qtbl_30,
    input  logic [15:0]  in_qtbl_31,
    input  logic [15:0]  in_qtbl_32,
    input  logic [15:0]  in_qtbl_33,
    input  logic [15:0]  in_qtbl_34,
    input  logic [15:0]  in_qtbl_35,
    input  logic [15:0]  in_qtbl_36,
    input  logic [15:0]  in_qtbl_37,
    input  logic [15:0]  in_qtbl_38,
    input  logic [15:0]  in_qtbl_39,
    input  logic [15:0]  in_qtbl_40,
    input  logic [15:0]  in_qtbl_41,
    input  logic [15:0]  in_qtbl_42,
    input  logic [15:0]  in_qtbl_43,
    input  logic [15:0]  in_qtbl_44,
    input  logic [15:0]  in_qtbl_45,
    input  logic [15:0]  in_qtbl_46,
    input  logic [15:0]  in_qtbl_47,
    input  logic [15:0]  in_qtbl_48,
    input  logic [15:0]  in_qtbl_49,
    input  logic [15:0]  in_qtbl_50,
    input  logic [15:0]  in_qtbl_51,
    input  logic [15:0]  in_qtbl_52,
    input  logic [15:0]  in_qtbl_53,
    input  logic [15:0]  in_qtbl_54,
    input  logic [15:0]  in_qtbl_55,
    input  logic [15:0]  in_qtbl_56,
    input  logic [15:0]  in_qtbl_57,
    input  logic [15:0]  in_qtbl_58,
    input  logic [15:0]  in_qtbl_59,
    input  logic [15:0]  in_qtbl_60,
    input  logic [15:0]  in_qtbl_61,
    input  logic [15:0]  in_qtbl_62,
    input  logic [15:0]  in_qtbl_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_coef_0,
    output logic [15:0]  out_coef_1,
    output logic [15:0]  out_coef_2,
    output logic [15:0]  out_coef_3,
    output logic [15:0]  out_coef_4,
    output logic [15:0]  out_coef_5,
    output logic [15:0]  out_coef_6,
    output logic [15:0]  out_coef_7,
    output logic [15:0]  out_coef_8,
    output logic [15:0]  out_coef_9,
    output logic [15:0]  out_coef_10,
    output logic [15:0]  out_coef_11,
    output logic [15:0]  out_coef_12,
    output logic [15:0]  out_coef_13,
    output logic [15:0]  out_coef_14,
    output logic [15:0]  out_coef_15,
    output logic [15:0]  out_coef_16,
    output logic [15:0]  out_coef_17,
    output logic [15:0]  out_coef_18,
    output logic [15:0]  out_coef_19,
    output logic [15:0]  out_coef_20,
    output logic [15:0]  out_coef_21,
    output logic [15:0]  out_coef_22,
    output logic [15:0]  out_coef_23,
    output logic [15:0]  out_coef_24,
    output logic [15:0]  out_coef_25,
    output logic [15:0]  out_coef_26,
    output logic [15:0]  out_coef_27,
    output logic [15:0]  out_coef_28,
    output logic [15:0]  out_coef_29,
    output logic [15:0]  out_coef_30,
    output logic [15:0]  out_coef_31,
    output logic [15:0]  out_coef_32,
    output logic [15:0]  out_coef_33,
    output logic [15:0]  out_coef_34,
    output logic [15:0]  out_coef_35,
    output logic [15:0]  out_coef_36,
    output logic [15:0]  out_coef_37,
    output logic [15:0]  out_coef_38,
    output logic [15:0]  out_coef_39,
    output logic [15:0]  out_coef_40,
    output logic [15:0]  out_coef_41,
    output logic [15:0]  out_coef_42,
    output logic [15:0]  out_coef_43,
    output logic [15:0]  out_coef_44,
    output logic [15:0]  out_coef_45,
    output logic [15:0]  out_coef_46,
    output logic [15:0]  out_coef_47,
    output logic [15:0]  out_coef_48,
    output logic [15:0]  out_coef_49,
    output logic [15:0]  out_coef_50,
    output logic [15:0]  out_coef_51,
    output logic [15:0]  out_coef_52,
    output logic [15:0]  out_coef_53,
    output logic [15:0]  out_coef_54,
    output logic [15:0]  out_coef_55,
    output logic [15:0]  out_coef_56,
    output logic [15:0]  out_coef_57,
    output logic [15:0]  out_coef_58,
    output logic [15:0]  out_coef_59,
    output logic [15:0]  out_coef_60,
    output logic [15:0]  out_coef_61,
    output logic [15:0]  out_coef_62,
    output logic [15:0]  out_coef_63
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
        out_coef_0  <= 16'h0000;
        out_coef_1  <= 16'h0000;
        out_coef_2  <= 16'h0000;
        out_coef_3  <= 16'h0000;
        out_coef_4  <= 16'h0000;
        out_coef_5  <= 16'h0000;
        out_coef_6  <= 16'h0000;
        out_coef_7  <= 16'h0000;
        out_coef_8  <= 16'h0000;
        out_coef_9  <= 16'h0000;
        out_coef_10 <= 16'h0000;
        out_coef_11 <= 16'h0000;
        out_coef_12 <= 16'h0000;
        out_coef_13 <= 16'h0000;
        out_coef_14 <= 16'h0000;
        out_coef_15 <= 16'h0000;
        out_coef_16 <= 16'h0000;
        out_coef_17 <= 16'h0000;
        out_coef_18 <= 16'h0000;
        out_coef_19 <= 16'h0000;
        out_coef_20 <= 16'h0000;
        out_coef_21 <= 16'h0000;
        out_coef_22 <= 16'h0000;
        out_coef_23 <= 16'h0000;
        out_coef_24 <= 16'h0000;
        out_coef_25 <= 16'h0000;
        out_coef_26 <= 16'h0000;
        out_coef_27 <= 16'h0000;
        out_coef_28 <= 16'h0000;
        out_coef_29 <= 16'h0000;
        out_coef_30 <= 16'h0000;
        out_coef_31 <= 16'h0000;
        out_coef_32 <= 16'h0000;
        out_coef_33 <= 16'h0000;
        out_coef_34 <= 16'h0000;
        out_coef_35 <= 16'h0000;
        out_coef_36 <= 16'h0000;
        out_coef_37 <= 16'h0000;
        out_coef_38 <= 16'h0000;
        out_coef_39 <= 16'h0000;
        out_coef_40 <= 16'h0000;
        out_coef_41 <= 16'h0000;
        out_coef_42 <= 16'h0000;
        out_coef_43 <= 16'h0000;
        out_coef_44 <= 16'h0000;
        out_coef_45 <= 16'h0000;
        out_coef_46 <= 16'h0000;
        out_coef_47 <= 16'h0000;
        out_coef_48 <= 16'h0000;
        out_coef_49 <= 16'h0000;
        out_coef_50 <= 16'h0000;
        out_coef_51 <= 16'h0000;
        out_coef_52 <= 16'h0000;
        out_coef_53 <= 16'h0000;
        out_coef_54 <= 16'h0000;
        out_coef_55 <= 16'h0000;
        out_coef_56 <= 16'h0000;
        out_coef_57 <= 16'h0000;
        out_coef_58 <= 16'h0000;
        out_coef_59 <= 16'h0000;
        out_coef_60 <= 16'h0000;
        out_coef_61 <= 16'h0000;
        out_coef_62 <= 16'h0000;
        out_coef_63 <= 16'h0000;
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_coef_0  <= $signed(in_coef_0)  * $unsigned(in_qtbl_0);
        out_coef_1  <= $signed(in_coef_1)  * $unsigned(in_qtbl_1);
        out_coef_2  <= $signed(in_coef_2)  * $unsigned(in_qtbl_2);
        out_coef_3  <= $signed(in_coef_3)  * $unsigned(in_qtbl_3);
        out_coef_4  <= $signed(in_coef_4)  * $unsigned(in_qtbl_4);
        out_coef_5  <= $signed(in_coef_5)  * $unsigned(in_qtbl_5);
        out_coef_6  <= $signed(in_coef_6)  * $unsigned(in_qtbl_6);
        out_coef_7  <= $signed(in_coef_7)  * $unsigned(in_qtbl_7);
        out_coef_8  <= $signed(in_coef_8)  * $unsigned(in_qtbl_8);
        out_coef_9  <= $signed(in_coef_9)  * $unsigned(in_qtbl_9);
        out_coef_10 <= $signed(in_coef_10) * $unsigned(in_qtbl_10);
        out_coef_11 <= $signed(in_coef_11) * $unsigned(in_qtbl_11);
        out_coef_12 <= $signed(in_coef_12) * $unsigned(in_qtbl_12);
        out_coef_13 <= $signed(in_coef_13) * $unsigned(in_qtbl_13);
        out_coef_14 <= $signed(in_coef_14) * $unsigned(in_qtbl_14);
        out_coef_15 <= $signed(in_coef_15) * $unsigned(in_qtbl_15);
        out_coef_16 <= $signed(in_coef_16) * $unsigned(in_qtbl_16);
        out_coef_17 <= $signed(in_coef_17) * $unsigned(in_qtbl_17);
        out_coef_18 <= $signed(in_coef_18) * $unsigned(in_qtbl_18);
        out_coef_19 <= $signed(in_coef_19) * $unsigned(in_qtbl_19);
        out_coef_20 <= $signed(in_coef_20) * $unsigned(in_qtbl_20);
        out_coef_21 <= $signed(in_coef_21) * $unsigned(in_qtbl_21);
        out_coef_22 <= $signed(in_coef_22) * $unsigned(in_qtbl_22);
        out_coef_23 <= $signed(in_coef_23) * $unsigned(in_qtbl_23);
        out_coef_24 <= $signed(in_coef_24) * $unsigned(in_qtbl_24);
        out_coef_25 <= $signed(in_coef_25) * $unsigned(in_qtbl_25);
        out_coef_26 <= $signed(in_coef_26) * $unsigned(in_qtbl_26);
        out_coef_27 <= $signed(in_coef_27) * $unsigned(in_qtbl_27);
        out_coef_28 <= $signed(in_coef_28) * $unsigned(in_qtbl_28);
        out_coef_29 <= $signed(in_coef_29) * $unsigned(in_qtbl_29);
        out_coef_30 <= $signed(in_coef_30) * $unsigned(in_qtbl_30);
        out_coef_31 <= $signed(in_coef_31) * $unsigned(in_qtbl_31);
        out_coef_32 <= $signed(in_coef_32) * $unsigned(in_qtbl_32);
        out_coef_33 <= $signed(in_coef_33) * $unsigned(in_qtbl_33);
        out_coef_34 <= $signed(in_coef_34) * $unsigned(in_qtbl_34);
        out_coef_35 <= $signed(in_coef_35) * $unsigned(in_qtbl_35);
        out_coef_36 <= $signed(in_coef_36) * $unsigned(in_qtbl_36);
        out_coef_37 <= $signed(in_coef_37) * $unsigned(in_qtbl_37);
        out_coef_38 <= $signed(in_coef_38) * $unsigned(in_qtbl_38);
        out_coef_39 <= $signed(in_coef_39) * $unsigned(in_qtbl_39);
        out_coef_40 <= $signed(in_coef_40) * $unsigned(in_qtbl_40);
        out_coef_41 <= $signed(in_coef_41) * $unsigned(in_qtbl_41);
        out_coef_42 <= $signed(in_coef_42) * $unsigned(in_qtbl_42);
        out_coef_43 <= $signed(in_coef_43) * $unsigned(in_qtbl_43);
        out_coef_44 <= $signed(in_coef_44) * $unsigned(in_qtbl_44);
        out_coef_45 <= $signed(in_coef_45) * $unsigned(in_qtbl_45);
        out_coef_46 <= $signed(in_coef_46) * $unsigned(in_qtbl_46);
        out_coef_47 <= $signed(in_coef_47) * $unsigned(in_qtbl_47);
        out_coef_48 <= $signed(in_coef_48) * $unsigned(in_qtbl_48);
        out_coef_49 <= $signed(in_coef_49) * $unsigned(in_qtbl_49);
        out_coef_50 <= $signed(in_coef_50) * $unsigned(in_qtbl_50);
        out_coef_51 <= $signed(in_coef_51) * $unsigned(in_qtbl_51);
        out_coef_52 <= $signed(in_coef_52) * $unsigned(in_qtbl_52);
        out_coef_53 <= $signed(in_coef_53) * $unsigned(in_qtbl_53);
        out_coef_54 <= $signed(in_coef_54) * $unsigned(in_qtbl_54);
        out_coef_55 <= $signed(in_coef_55) * $unsigned(in_qtbl_55);
        out_coef_56 <= $signed(in_coef_56) * $unsigned(in_qtbl_56);
        out_coef_57 <= $signed(in_coef_57) * $unsigned(in_qtbl_57);
        out_coef_58 <= $signed(in_coef_58) * $unsigned(in_qtbl_58);
        out_coef_59 <= $signed(in_coef_59) * $unsigned(in_qtbl_59);
        out_coef_60 <= $signed(in_coef_60) * $unsigned(in_qtbl_60);
        out_coef_61 <= $signed(in_coef_61) * $unsigned(in_qtbl_61);
        out_coef_62 <= $signed(in_coef_62) * $unsigned(in_qtbl_62);
        out_coef_63 <= $signed(in_coef_63) * $unsigned(in_qtbl_63);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule